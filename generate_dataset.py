#!/usr/bin/env python3
"""
Synthetic dataset generator for the TBX finance-assistant hackathon schema.

Builds bank / account / transaction tables that match "TBX - Database Schema.md",
reproducing the real-world messiness of the 10 sample rows:
  - 9 payment-rail narration formats, each putting the counterparty in a
    different position inside `description`
  - deliberately colliding vendor names (the SELECTION / SELECTRICITY / SELECT family)
  - alias spellings of the same vendor
  - Bajaj Finance loan references (ZBFLCTP...PBL########) on inward collections
  - deterministic "encrypted" utr_number values sharing a fixed 21-char prefix
  - positive amounts with direction carried by transaction_type

Stdlib only. Seeded, so every run produces the same data.

Usage:
    python3 generate_dataset.py                      # 100k transactions
    python3 generate_dataset.py --transactions 500000
    python3 generate_dataset.py --dirty              # inject malformed rows
"""

import argparse, base64, csv, hashlib, os, random, sys
from collections import defaultdict
from datetime import datetime, timedelta, date

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

SEED = 20260905
DATA_START = date(2025, 1, 1)
DATA_END = date(2026, 9, 5)          # = "today" for the hackathon, so "last month" has data

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ----------------------------------------------------------------------------
# Reference data
# ----------------------------------------------------------------------------

BANKS = [
    ("HDFC", "HDFC BANK LIMITED",              14),
    ("ICIC", "ICICI BANK LIMITED",             12),
    ("SBIN", "STATE BANK OF INDIA",            11),
    ("UTIB", "AXIS BANK LIMITED",              15),
    ("KKBK", "KOTAK MAHINDRA BANK LIMITED",    13),
    ("CNRB", "CANARA BANK",                    13),
    ("UBIN", "UNION BANK OF INDIA",            15),
    ("AUBL", "AU SMALL FINANCE BANK LIMITED",  16),
    ("TMBL", "TAMILNAD MERCANTILE BANK LIMITED", 15),
    ("RATN", "RBL BANK LIMITED",               12),
]
BANK_CODES = [b[0] for b in BANKS]
ACCT_LEN = {b[0]: b[2] for b in BANKS}

CITIES = [
    "DAHISAR EAST", "ANDHERI WEST", "SAKET DELHI", "KORAMANGALA BLR", "T NAGAR CHENNAI",
    "SALT LAKE KOL", "HITEC CITY HYD", "VIMAN NAGAR PUN", "SATELLITE AHM", "VAISHALI GZB",
    "RAJOURI GARDEN", "BANJARA HILLS", "ADYAR CHENNAI", "THANE WEST", "SECTOR 62 NOIDA",
    # a mall name that contains "SELECT" but is a LOCATION, not a vendor -- from the real sample row
    "SELECT CITY SAKET DELHI", "SELECT CITYWALK DELHI",
]

# (canonical_name, category, [aliases])  -- aliases are alternate spellings seen in narrations
VENDORS = [
    # ---- the deliberate near-collision family -------------------------------
    ("SELECTION ELECTRONICS",            "inventory",   ["SELECTION ELECTRONICS", "SELECTION ELECTRONIC", "SELECTION ELECTRONICS PVT LTD"]),
    ("SELECTION MOBILE",                 "inventory",   ["SELECTION MOBILE", "SELECTION MOBILES", "SELECTION MOBILE SHOP"]),
    ("NAVYUG SELECTION",                 "inventory",   ["NAVYUG SELECTION", "NAVYUG SELECTIONS"]),
    ("SELECTIONMALIGAI",                 "inventory",   ["SELECTIONMALIGAI", "SELECTION MALIGAI"]),
    ("UMANG SELECTION",                  "inventory",   ["UMANG SELECTION", "UMANG SELECTIONHAPURBPES"]),
    ("SELECT MOBILES",                   "inventory",   ["SELECT MOBILES", "SELECT MOBILE"]),
    ("SELECTRICITY TWO PRIVATE LIMITED", "utilities",   ["SELECTRICITY TWO PRIVATE LIMITED", "SELECTRICITY TWO PVT LTD"]),
    ("SELECTRICITY ONE PRIVATE LIMITED", "utilities",   ["SELECTRICITY ONE PRIVATE LIMITED", "SELECTRICITY ONE PVT LTD"]),
    ("RELIANCE DIGITAL RETAIL LTD",      "inventory",   ["RELIANCEDIGITAL RETAIL LTD", "RELIANCE DIGITAL RETAIL LTD", "RELIANCE DIGITAL"]),
    # ---- alias families (same vendor, different spellings) ------------------
    ("INFOSYS LIMITED",                  "professional_services", ["INFOSYS LIMITED", "INFOSYS LTD", "INFOSYS"]),
    ("TATA CONSULTANCY SERVICES LTD",    "professional_services", ["TATA CONSULTANCY SERVICES LTD", "TCS LIMITED", "TATA CONSULTANCY SERV"]),
    ("LARSEN AND TOUBRO LIMITED",        "professional_services", ["LARSEN AND TOUBRO LIMITED", "L AND T LTD", "LARSEN TOUBRO"]),
    ("AMAZON SELLER SERVICES PVT LTD",   "software",    ["AMAZON SELLER SERVICES PVT LTD", "AMAZON SELLER SERV", "AMAZON WEB SERVICES"]),
    # ---- utilities ----------------------------------------------------------
    ("TATA POWER COMPANY LIMITED",       "utilities",   ["TATA POWER COMPANY LIMITED", "TATA POWER"]),
    ("ADANI ELECTRICITY MUMBAI LTD",     "utilities",   ["ADANI ELECTRICITY MUMBAI LTD", "ADANI ELECTRICITY"]),
    ("BESCOM BANGALORE",                 "utilities",   ["BESCOM BANGALORE", "BESCOM"]),
    ("MAHANAGAR GAS LIMITED",            "utilities",   ["MAHANAGAR GAS LIMITED", "MAHANAGAR GAS"]),
    ("BHARTI AIRTEL LIMITED",            "utilities",   ["BHARTI AIRTEL LIMITED", "AIRTEL", "BHARTI AIRTEL"]),
    ("RELIANCE JIO INFOCOMM LTD",        "utilities",   ["RELIANCE JIO INFOCOMM LTD", "JIO INFOCOMM"]),
    # ---- logistics ----------------------------------------------------------
    ("BLUE DART EXPRESS LIMITED",        "logistics",   ["BLUE DART EXPRESS LIMITED", "BLUEDART EXPRESS", "BLUE DART"]),
    ("DELHIVERY LIMITED",                "logistics",   ["DELHIVERY LIMITED", "DELHIVERY"]),
    ("GATI KWE PRIVATE LIMITED",         "logistics",   ["GATI KWE PRIVATE LIMITED", "GATI KWE"]),
    ("VRL LOGISTICS LIMITED",            "logistics",   ["VRL LOGISTICS LIMITED", "VRL LOGISTICS"]),
    ("SAFEXPRESS PRIVATE LIMITED",       "logistics",   ["SAFEXPRESS PRIVATE LIMITED", "SAFEXPRESS"]),
    # ---- software / saas ----------------------------------------------------
    ("MICROSOFT REGIONAL SALES PTE",     "software",    ["MICROSOFT REGIONAL SALES PTE", "MICROSOFT REGIONAL"]),
    ("GOOGLE CLOUD INDIA PVT LTD",       "software",    ["GOOGLE CLOUD INDIA PVT LTD", "GOOGLE CLOUD INDIA"]),
    ("ZOHO CORPORATION PVT LTD",         "software",    ["ZOHO CORPORATION PVT LTD", "ZOHO CORP"]),
    ("FRESHWORKS TECHNOLOGIES",          "software",    ["FRESHWORKS TECHNOLOGIES", "FRESHWORKS"]),
    ("ATLASSIAN PTY LTD",                "software",    ["ATLASSIAN PTY LTD", "ATLASSIAN"]),
    ("RAZORPAY SOFTWARE PVT LTD",        "software",    ["RAZORPAY SOFTWARE PVT LTD", "RAZORPAY"]),
    # ---- rent / facilities --------------------------------------------------
    ("EMBASSY OFFICE PARKS REIT",        "rent",        ["EMBASSY OFFICE PARKS REIT", "EMBASSY OFFICE PARKS"]),
    ("DLF CYBER CITY DEVELOPERS",        "rent",        ["DLF CYBER CITY DEVELOPERS", "DLF CYBER CITY"]),
    ("RMZ CORP PRIVATE LIMITED",         "rent",        ["RMZ CORP PRIVATE LIMITED", "RMZ CORP"]),
    ("BRIGADE ENTERPRISES LIMITED",      "rent",        ["BRIGADE ENTERPRISES LIMITED", "BRIGADE ENTERPRISES"]),
    # ---- marketing ----------------------------------------------------------
    ("GOOGLE INDIA PVT LTD ADS",         "marketing",   ["GOOGLE INDIA PVT LTD ADS", "GOOGLE INDIA ADS"]),
    ("META PLATFORMS IRELAND LTD",       "marketing",   ["META PLATFORMS IRELAND LTD", "META PLATFORMS"]),
    ("DENTSU AEGIS NETWORK INDIA",       "marketing",   ["DENTSU AEGIS NETWORK INDIA", "DENTSU AEGIS"]),
    ("OGILVY AND MATHER PVT LTD",        "marketing",   ["OGILVY AND MATHER PVT LTD", "OGILVY MATHER"]),
    # ---- travel -------------------------------------------------------------
    ("MAKEMYTRIP INDIA PVT LTD",         "travel",      ["MAKEMYTRIP INDIA PVT LTD", "MAKEMYTRIP INDIA"]),
    ("INTERGLOBE AVIATION LIMITED",      "travel",      ["INTERGLOBE AVIATION LIMITED", "INTERGLOBE AVIATION", "INDIGO AIRLINES"]),
    ("YATRA ONLINE LIMITED",             "travel",      ["YATRA ONLINE LIMITED", "YATRA ONLINE"]),
    ("OYO HOTELS AND HOMES PVT",         "travel",      ["OYO HOTELS AND HOMES PVT", "OYO HOTELS"]),
    # ---- professional services ---------------------------------------------
    ("DELOITTE HASKINS AND SELLS",       "professional_services", ["DELOITTE HASKINS AND SELLS", "DELOITTE HASKINS"]),
    ("KPMG ASSURANCE AND CONSULTING",    "professional_services", ["KPMG ASSURANCE AND CONSULTING", "KPMG ASSURANCE"]),
    ("SHARDUL AMARCHAND MANGALDAS",      "professional_services", ["SHARDUL AMARCHAND MANGALDAS", "SHARDUL AMARCHAND"]),
    ("QUESS CORP LIMITED",               "professional_services", ["QUESS CORP LIMITED", "QUESS CORP"]),
    # ---- insurance / tax ----------------------------------------------------
    ("ICICI LOMBARD GENERAL INSURANCE",  "insurance",   ["ICICI LOMBARD GENERAL INSURANCE", "ICICI LOMBARD"]),
    ("HDFC LIFE INSURANCE CO LTD",       "insurance",   ["HDFC LIFE INSURANCE CO LTD", "HDFC LIFE"]),
    ("GST PAYMENT CBDT",                 "tax",         ["GST PAYMENT CBDT", "GST PAYMENT"]),
    ("INCOME TAX DEPARTMENT TDS",        "tax",         ["INCOME TAX DEPARTMENT TDS", "INCOMETAX TDS"]),
    # ---- inventory / trade --------------------------------------------------
    ("CROMA INFINITI RETAIL LTD",        "inventory",   ["CROMA INFINITI RETAIL LTD", "CROMA INFINITI"]),
    ("VIJAY SALES INDIA PVT LTD",        "inventory",   ["VIJAY SALES INDIA PVT LTD", "VIJAY SALES"]),
    ("SANGEETHA MOBILES PVT LTD",        "inventory",   ["SANGEETHA MOBILES PVT LTD", "SANGEETHA MOBILES"]),
    ("POORVIKA MOBILES PVT LTD",         "inventory",   ["POORVIKA MOBILES PVT LTD", "POORVIKA MOBILES"]),
    ("BAJAJ ELECTRICALS LIMITED",        "inventory",   ["BAJAJ ELECTRICALS LIMITED", "BAJAJ ELECTRICALS"]),
    ("HAVELLS INDIA LIMITED",            "inventory",   ["HAVELLS INDIA LIMITED", "HAVELLS INDIA"]),
    ("VOLTAS LIMITED",                   "inventory",   ["VOLTAS LIMITED", "VOLTAS LTD"]),
    ("BLUE STAR LIMITED",                "inventory",   ["BLUE STAR LIMITED", "BLUE STAR LTD"]),
]

FIRST = ["Gautam", "Paresh", "Anjali", "Rakesh", "Sunita", "Vikram", "Meera", "Arjun", "Kavita",
         "Sanjay", "Deepa", "Nitin", "Priya", "Rahul", "Shweta", "Manoj", "Farah", "Imran",
         "Lakshmi", "Rohit", "Ananya", "Suresh", "Pooja", "Karthik", "Neha", "Vivek", "Divya",
         "Ashok", "Ritu", "Harish", "Sneha", "Prakash", "Ayesha", "Naveen", "Swati", "Tarun"]
LAST  = ["singh", "Ghase", "Sharma", "Patel", "Reddy", "Nair", "Iyer", "Mehta", "Kulkarni",
         "Bose", "Chauhan", "Desai", "Joshi", "Rao", "Verma", "Gupta", "Khan", "Pillai",
         "Bhatt", "Menon", "Shetty", "Agarwal", "Dubey", "Saxena", "Kapoor", "Malhotra"]

ENTITY_STEMS = [
    "SELECTION RETAIL", "NAVYUG TRADERS", "UMANG ENTERPRISES", "SELECTRICITY HOLDINGS",
    "ORBIT COMMERCE", "MERIDIAN SUPPLY", "PINNACLE DISTRIBUTORS", "ARCLIGHT VENTURES",
    "KESHAV INDUSTRIES", "SANCHAY TRADING", "VISTARA MERCHANTS", "AMBER LOGISTICS",
    "NORTHSTAR RETAIL", "GRANITE COMMERCE", "BLUEWAVE TRADERS", "SUNRISE DISTRIBUTORS",
    "CRESCENT SUPPLY", "IRONWOOD VENTURES", "SAPPHIRE RETAIL", "TERRAFIRMA TRADING",
    "VELOCITY MERCHANTS", "HARBOUR ENTERPRISES", "KINETIC DISTRIBUTORS", "ZENITH COMMERCE",
    "PARAMOUNT SUPPLY", "EASTGATE TRADERS", "SILVERLINE RETAIL", "MOMENTUM VENTURES",
    "CLEARWATER TRADING", "REDSTONE COMMERCE", "APEX MERCHANTS", "LIGHTHOUSE SUPPLY",
    "FALCON DISTRIBUTORS", "MERCURY RETAIL", "OASIS ENTERPRISES", "TITAN TRADING",
    "COMPASS COMMERCE", "EVEREST SUPPLY", "LOTUS DISTRIBUTORS", "PHOENIX VENTURES",
]
ENTITY_SUFFIX = ["PRIVATE LIMITED", "LLP", "AND SONS", "PVT LTD", "LIMITED", "ENTERPRISES"]

CHARGE_NARRATIONS = [
    ("IMPS charges", 5.0, 25.0), ("NEFT charges", 2.0, 30.0), ("RTGS charges", 20.0, 60.0),
    ("Cheque Deposits", 50.0, 500.0), ("SMS Alert Charges", 15.0, 30.0),
    ("AMC Charges", 200.0, 800.0), ("Cash Handling Charges", 100.0, 1200.0),
    ("Chq Return Charges", 300.0, 900.0), ("Stop Payment Charges", 100.0, 400.0),
    ("Account Maintenance Fee", 150.0, 600.0),
]

# category -> (log-mean, log-sigma) for amount in rupees
AMOUNT_PROFILE = {
    "inventory":             (11.2, 1.15),
    "utilities":             (10.1, 0.95),
    "logistics":             (9.6,  1.05),
    "software":              (10.8, 1.10),
    "rent":                  (13.0, 0.60),
    "marketing":             (11.6, 1.20),
    "travel":                (9.9,  1.00),
    "professional_services": (12.1, 1.05),
    "insurance":             (11.4, 0.85),
    "tax":                   (12.4, 1.00),
    "loan_collection":       (10.6, 1.05),
    "loan_disbursement":     (12.6, 0.90),
    "salary":                (11.0, 0.55),
    "bank_charges":          (4.5,  0.80),
}

RAILS = ["FT_SPACED", "FT_COMPACT", "UPI", "NEFT_SPACED", "NEFT_SLASH",
         "IMPS_OW", "IMPS_P2A", "RBL_INWARD", "RTGS", "CHARGES"]

UTR_PREFIX_BYTES = base64.b64decode("jhI5nAdyb1qOEjmcB3JvWg==")[:15]   # reproduces the 21-char shared prefix

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def uuid4(rnd):
    h = "%032x" % rnd.getrandbits(128)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

def encrypt_utr(plaintext):
    """Deterministic 'encryption': same plaintext -> same ciphertext, fixed 15-byte prefix.
    Mirrors the sample data, where all UTRs share the first 21 base64 chars and come in
    three lengths. Length is derived from the plaintext, NOT randomly, so both legs of an
    internal transfer carry an identical utr_number -- that is what makes reconciliation
    by UTR possible."""
    body = hashlib.sha256(plaintext.encode()).digest()
    raw = UTR_PREFIX_BYTES + body[:(27, 30, 33)[body[0] % 3]]
    return base64.b64encode(raw).decode()

def ifsc(rnd, bank_code):
    return f"{bank_code}0{rnd.randint(0, 999999):06d}"

def acct_number(rnd, bank_code):
    n = ACCT_LEN[bank_code]
    lead = rnd.choice("23456789")
    return lead + "".join(rnd.choice("0123456789") for _ in range(n - 1))

def loan_ref(rnd):
    mid = "".join(rnd.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(3))
    return f"ZBFLCTP{mid}PBL{rnd.randint(10000000, 99999999)}"

def money(rnd, category, scale=1.0):
    import math
    mu, sigma = AMOUNT_PROFILE[category]
    v = math.exp(rnd.gauss(mu, sigma)) * scale
    v = max(1.0, min(v, 250_000_000.0))
    return round(v, 2)

def pick_date(rnd, span_days, weights):
    """Recent months are busier; Sundays are quiet; business hours dominate."""
    day = rnd.choices(range(span_days), weights=weights, k=1)[0]
    d = DATA_START + timedelta(days=day)
    if d.weekday() == 6 and rnd.random() < 0.75:
        d = d - timedelta(days=rnd.randint(1, 2))
    if rnd.random() < 0.82:
        hh = rnd.randint(9, 19)
    else:
        hh = rnd.choice([0, 1, 2, 3, 4, 5, 6, 7, 8, 20, 21, 22, 23])
    return datetime(d.year, d.month, d.day, hh, rnd.randint(0, 59), rnd.randint(0, 59),
                    rnd.randint(0, 999999))

def fmt_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")

# ----------------------------------------------------------------------------
# Narration builders -- one per rail, counterparty in a different position
# ----------------------------------------------------------------------------

def build_narration(rnd, rail, name, bank_code, counter_acct, ldate):
    """Returns (description, transaction_reference_id, utr_plaintext_or_None, loan_ref_or_None)."""
    if rail == "FT_SPACED":
        ref = str(rnd.randint(10000000, 99999999))
        desc = f"FT -  {ref} -  {counter_acct} - {name}   {rnd.choice(CITIES)}"
        return desc, str(rnd.randint(1000000000, 1999999999)), f"FT{ref}", None

    if rail == "FT_COMPACT":
        tag = "".join(rnd.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(6))
        ref = f"{tag}{rnd.randint(1000000000, 9999999999)}"
        desc = f"FT-{ref}-{name}   {rnd.choice(CITIES)}"
        return desc, str(rnd.randint(1000000000, 1999999999)), f"FT{ref}", None

    if rail == "UPI":
        upi_ref = str(rnd.randint(100000000000, 999999999999))
        stamp = ldate.strftime("%y%m%d") + f"{rnd.randint(0, 999999999):09d}"
        desc = (f"UPI-{name}-XXXXXX{rnd.randint(1000, 9999)}-"
                f"{ifsc(rnd, rnd.choice(BANK_CODES))}-{upi_ref}-{stamp}")
        return desc, upi_ref, None, None

    if rail == "NEFT_SPACED":
        ref = str(rnd.randint(10000000, 99999999))
        tail = f" {''.join(rnd.choice('ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(3))}{rnd.randint(10000, 99999)}" if rnd.random() < 0.30 else ""
        desc = f"NEFT  - {ifsc(rnd, rnd.choice(BANK_CODES))} - {ref} - {counter_acct} - {name}{tail}"
        return desc, f"{bank_code}H{rnd.randint(10000000000, 99999999999)}", f"NEFT{ref}", None

    if rail == "NEFT_SLASH":
        ref = f"{rnd.randint(0, 999999999999):012d}"
        desc = f"NEFT/{ref}/{rnd.choice(BANK_CODES)}/{name}"
        return desc, f"S{rnd.randint(1000000, 99999999)}", None, None

    if rail == "IMPS_OW":
        ref = str(rnd.randint(100000000000, 999999999999))
        desc = f"IMPS OW/{ref}/{name}/{rnd.choice(BANK_CODES)}/{counter_acct}"
        rid = None if rnd.random() < 0.35 else f"S{rnd.randint(1000000, 99999999)}"
        return desc, rid, None, None

    if rail == "IMPS_P2A":
        ref = str(rnd.randint(100000000000, 999999999999))
        lr = loan_ref(rnd)
        desc = (f"IMPS/P2A/{ref}/{rnd.choice(BANK_CODES)}/{counter_acct}/00/INET/"
                f"{rnd.randint(1000, 9999)}/{name}/{lr}/INWD{rnd.randint(10, 99)}")
        return desc, f"S{rnd.randint(1000000, 99999999)}", None, lr

    if rail == "RBL_INWARD":
        rbl = f"RATNR5{ldate.strftime('%Y%m%d')}{rnd.randint(0, 99999999):08d}"
        lr = loan_ref(rnd)
        desc = f"R/{rbl}/{lr}//{name}/{rbl} /{name}"
        return desc, f"S{rnd.randint(1000000, 99999999)}", None, lr

    if rail == "RTGS":
        ref = str(rnd.randint(10000000, 99999999))
        desc = f"RTGS  - {ifsc(rnd, rnd.choice(BANK_CODES))} - {ref} - {counter_acct} - {name}"
        return desc, f"{bank_code}R{rnd.randint(10000000000, 99999999999)}", f"RTGS{ref}", None

    raise ValueError(rail)

# ----------------------------------------------------------------------------
# Generation
# ----------------------------------------------------------------------------

def generate(n_txn, n_accounts, n_entities, dirty):
    rnd = random.Random(SEED)

    # -- entities -------------------------------------------------------------
    entities = []
    for i in range(n_entities):
        stem = ENTITY_STEMS[i % len(ENTITY_STEMS)]
        suffix = ENTITY_SUFFIX[i % len(ENTITY_SUFFIX)]
        entities.append((uuid4(rnd), f"{stem} {suffix}"))

    # -- accounts -------------------------------------------------------------
    accounts = []
    for _ in range(n_accounts):
        eid, _ = rnd.choice(entities)
        bc = rnd.choices(BANK_CODES, weights=[26, 16, 13, 12, 8, 6, 6, 5, 4, 4], k=1)[0]
        accounts.append({
            "account_id": uuid4(rnd),
            "entity_id": eid,
            "account_number": acct_number(rnd, bc),
            "program_id": rnd.choices([4, 21, 46], weights=[3, 5, 2], k=1)[0],
            "bank_code": bc,
        })
    acct_ids = [a["account_id"] for a in accounts]
    acct_by_id = {a["account_id"]: a for a in accounts}

    # -- vendor alias pool ----------------------------------------------------
    vendor_rows = []
    for vid, (canon, cat, aliases) in enumerate(VENDORS, start=1):
        vendor_rows.append({"counterparty_id": f"CP{vid:04d}", "canonical_name": canon,
                            "category": cat, "aliases": aliases})
    # individuals (used by IMPS OW / NEFT slash)
    people = []
    for i in range(140):
        nm = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
        people.append({"counterparty_id": f"PP{i+1:04d}", "canonical_name": nm.upper(),
                       "category": "salary", "aliases": [nm, nm.upper(), nm.lower()]})
    all_cp = vendor_rows + people
    cp_by_id = {c["counterparty_id"]: c for c in all_cp}

    # give each vendor a "home" amount scale so anomalies are detectable
    cp_scale = {c["counterparty_id"]: rnd.uniform(0.45, 2.2) for c in all_cp}

    # -- date weighting -------------------------------------------------------
    span = (DATA_END - DATA_START).days + 1
    weights = []
    for d in range(span):
        cur = DATA_START + timedelta(days=d)
        growth = 1.0 + 1.1 * (d / span)                       # volume grows over time
        weekend = 0.28 if cur.weekday() == 6 else (0.72 if cur.weekday() == 5 else 1.0)
        monthend = 1.9 if cur.day >= 27 or cur.day <= 3 else 1.0   # month-end payment runs
        weights.append(growth * weekend * monthend)

    txns = []
    truth = []

    def emit(account_id, dt, ttype, desc, amount, rid, utr_plain, cp_id, rail,
             category, loan, recon, match_id, anomaly):
        tid = uuid4(rnd)
        utr = encrypt_utr(utr_plain) if utr_plain else None
        txns.append({
            "transaction_id": tid,
            "account_id": account_id,
            "transaction_date": fmt_ts(dt),
            "transaction_type": ttype,
            "description": desc,
            "transaction_amount": f"{amount:.2f}",
            "transaction_reference_id": rid or "",
            "utr_number": utr or "",
        })
        truth.append({
            "transaction_id": tid,
            "counterparty_id": cp_id or "",
            "counterparty_name": cp_by_id[cp_id]["canonical_name"] if cp_id else "",
            "rail": rail,
            "category": category,
            "loan_ref": loan or "",
            "recon_status": recon,
            "recon_match_id": match_id or "",
            "is_anomaly": "1" if anomaly else "0",
        })
        return tid

    target = n_txn
    while len(txns) < target:
        roll = rnd.random()

        # ---- 1. internal transfer pair: same UTR + same reference, two accounts
        if roll < 0.16 and len(txns) + 2 <= target:
            a, b = rnd.sample(acct_ids, 2)
            dt = pick_date(rnd, span, weights)
            cp = rnd.choice(vendor_rows)
            amt = money(rnd, cp["category"], cp_scale[cp["counterparty_id"]])
            rail = rnd.choice(["NEFT_SPACED", "RTGS", "FT_SPACED"])
            name = rnd.choice(cp["aliases"]).upper()
            desc_a, rid, utrp, _ = build_narration(
                rnd, rail, name, acct_by_id[a]["bank_code"], acct_by_id[b]["account_number"], dt.date())
            dt2 = dt + timedelta(minutes=rnd.randint(1, 240))
            if dt2.date() > DATA_END:
                dt2 = dt
            desc_b, _, _, _ = build_narration(
                rnd, rail, name, acct_by_id[b]["bank_code"], acct_by_id[a]["account_number"], dt2.date())
            id1 = emit(a, dt, "debit", desc_a, amt, rid, utrp, cp["counterparty_id"], rail,
                       cp["category"], None, "matched_pair", None, False)
            id2 = emit(b, dt2, "credit", desc_b, amt, rid, utrp, cp["counterparty_id"], rail,
                       cp["category"], None, "matched_pair", id1, False)
            truth[-2]["recon_match_id"] = id2
            continue

        # ---- 2. inward loan collection carrying a ZBFL...PBL reference --------
        if roll < 0.34:
            acct = rnd.choice(acct_ids)
            dt = pick_date(rnd, span, weights)
            rail = rnd.choice(["IMPS_P2A", "RBL_INWARD"])
            cp = rnd.choice(vendor_rows)
            name = rnd.choice(cp["aliases"]).upper()
            amt = money(rnd, "loan_collection")
            desc, rid, utrp, lr = build_narration(
                rnd, rail, name, acct_by_id[acct]["bank_code"],
                acct_number(rnd, rnd.choice(BANK_CODES)), dt.date())
            emit(acct, dt, "credit", desc, amt, rid, utrp, cp["counterparty_id"], rail,
                 "loan_collection", lr, "matched_ref", None, False)
            continue

        # ---- 3. bank charges (no counterparty) --------------------------------
        if roll < 0.42:
            acct = rnd.choice(acct_ids)
            dt = pick_date(rnd, span, weights)
            label, lo, hi = rnd.choice(CHARGE_NARRATIONS)
            amt = round(rnd.uniform(lo, hi), 2)
            emit(acct, dt, "debit", label, amt, None, None, None, "CHARGES",
                 "bank_charges", None, "not_applicable", None, False)
            continue

        # ---- 4. person-to-person / payroll ------------------------------------
        if roll < 0.55:
            acct = rnd.choice(acct_ids)
            dt = pick_date(rnd, span, weights)
            cp = rnd.choice(people)
            rail = rnd.choice(["IMPS_OW", "NEFT_SLASH", "UPI"])
            name = rnd.choice(cp["aliases"])
            amt = money(rnd, "salary", cp_scale[cp["counterparty_id"]])
            desc, rid, utrp, _ = build_narration(
                rnd, rail, name, acct_by_id[acct]["bank_code"],
                acct_number(rnd, rnd.choice(BANK_CODES)), dt.date())
            emit(acct, dt, "debit", desc, amt, rid, utrp, cp["counterparty_id"], rail,
                 "salary", None, "unmatched", None, False)
            continue

        # ---- 5. ordinary vendor payout ----------------------------------------
        acct = rnd.choice(acct_ids)
        dt = pick_date(rnd, span, weights)
        cp = rnd.choice(vendor_rows)
        rail = rnd.choices(
            ["FT_SPACED", "FT_COMPACT", "UPI", "NEFT_SPACED", "NEFT_SLASH", "RTGS"],
            weights=[18, 10, 22, 26, 12, 12], k=1)[0]
        name = rnd.choice(cp["aliases"]).upper()
        anomaly = rnd.random() < 0.0016
        scale = cp_scale[cp["counterparty_id"]] * (rnd.uniform(9, 38) if anomaly else 1.0)
        amt = money(rnd, cp["category"], scale)
        ttype = "credit" if rnd.random() < 0.12 else "debit"
        desc, rid, utrp, _ = build_narration(
            rnd, rail, name, acct_by_id[acct]["bank_code"],
            acct_number(rnd, rnd.choice(BANK_CODES)), dt.date())
        emit(acct, dt, ttype, desc, amt, rid, utrp, cp["counterparty_id"], rail,
             cp["category"], None, "unmatched", None, anomaly)

    txns = txns[:target]
    truth = truth[:target]

    # -- balances derived from the transactions (credits positive, debits negative)
    net = defaultdict(float)
    for t in txns:
        amt = float(t["transaction_amount"])
        net[t["account_id"]] += amt if t["transaction_type"] == "credit" else -amt
    for a in accounts:
        a["available_balance"] = f"{round(net[a['account_id']], 2):.2f}"

    # -- optional dirty rows (mirrors the defects found in the sample fixtures) --
    dirty_notes = []
    if dirty:
        bad = dict(txns[0])
        bad["transaction_id"] = "0178b656-4a7d-98e8-9540f6e24caf"   # 4 groups, 31 chars
        txns.append(bad)
        truth.append(dict(truth[0], transaction_id=bad["transaction_id"]))
        dirty_notes.append("1 transaction with a malformed 31-char UUID (4 groups instead of 5)")
        for i in range(4):
            accounts[i]["entity_id"] = accounts[i + 5]["account_id"]  # id-space collision
        dirty_notes.append("4 accounts whose entity_id reuses another row's account_id")

    return accounts, entities, txns, truth, all_cp, dirty_notes

# ----------------------------------------------------------------------------
# Writers
# ----------------------------------------------------------------------------

def write_csv(path, rows, cols):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def sql_str(v):
    if v is None or v == "":
        return "NULL"
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"

def write_mysql(path, banks, accounts, txns):
    with open(path, "w") as f:
        f.write("SET FOREIGN_KEY_CHECKS=0;\nSET NAMES utf8mb4;\n\n")
        f.write("INSERT INTO bank (bank_code, bank_name) VALUES\n")
        f.write(",\n".join(f"({sql_str(b[0])}, {sql_str(b[1])})" for b in banks) + ";\n\n")

        f.write("INSERT INTO account (account_id, entity_id, account_number, program_id, available_balance, bank_code) VALUES\n")
        f.write(",\n".join(
            f"({sql_str(a['account_id'])}, {sql_str(a['entity_id'])}, {sql_str(a['account_number'])}, "
            f"{a['program_id']}, {a['available_balance']}, {sql_str(a['bank_code'])})"
            for a in accounts) + ";\n\n")

        cols = ("transaction_id, account_id, transaction_date, transaction_type, description, "
                "transaction_amount, transaction_reference_id, utr_number")
        B = 500
        for i in range(0, len(txns), B):
            chunk = txns[i:i + B]
            f.write(f"INSERT INTO transaction ({cols}) VALUES\n")
            f.write(",\n".join(
                f"({sql_str(t['transaction_id'])}, {sql_str(t['account_id'])}, {sql_str(t['transaction_date'])}, "
                f"{sql_str(t['transaction_type'])}, {sql_str(t['description'])}, {t['transaction_amount']}, "
                f"{sql_str(t['transaction_reference_id'])}, {sql_str(t['utr_number'])})"
                for t in chunk) + ";\n")
        f.write("\nSET FOREIGN_KEY_CHECKS=1;\n")

def load_mysql(banks, accounts, txns):
    """Apply data/schema.sql to the configured MySQL database and load all rows.

    Connection settings come from .env (see finassist.db). Any existing data in
    bank / account / `transaction` is dropped -- the DDL leads with DROP TABLE.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from finassist import db as _db

    schema = open(os.path.join(OUT, "schema.sql")).read()
    cx = _db.connect()
    cx.executescript(schema)
    cx.executemany("INSERT INTO bank (bank_code, bank_name) VALUES (?,?)",
                   [(b[0], b[1]) for b in banks])
    cx.executemany(
        "INSERT INTO account (account_id, entity_id, account_number, program_id, "
        "available_balance, bank_code) VALUES (?,?,?,?,?,?)",
        [(a["account_id"], a["entity_id"], a["account_number"], a["program_id"],
          float(a["available_balance"]), a["bank_code"]) for a in accounts])

    rows = [(t["transaction_id"], t["account_id"], t["transaction_date"],
             t["transaction_type"], t["description"], float(t["transaction_amount"]),
             t["transaction_reference_id"] or None, t["utr_number"] or None) for t in txns]
    B = 2000
    for i in range(0, len(rows), B):
        cx.executemany(
            "INSERT IGNORE INTO `transaction` (transaction_id, account_id, transaction_date, "
            "transaction_type, description, transaction_amount, transaction_reference_id, "
            "utr_number) VALUES (?,?,?,?,?,?,?,?)", rows[i:i + B])
    cx.commit()
    cx.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transactions", type=int, default=100_000)
    ap.add_argument("--accounts", type=int, default=80)
    ap.add_argument("--entities", type=int, default=48)
    ap.add_argument("--dirty", action="store_true")
    ap.add_argument("--no-load", action="store_true",
                    help="skip loading into MySQL; only write CSV/SQL files")
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    print(f"generating {a.transactions:,} transactions across {a.accounts} accounts ...", flush=True)
    accounts, entities, txns, truth, cps, dirty_notes = generate(
        a.transactions, a.accounts, a.entities, a.dirty)

    write_csv(f"{OUT}/bank.csv", [{"bank_code": b[0], "bank_name": b[1]} for b in BANKS],
              ["bank_code", "bank_name"])
    write_csv(f"{OUT}/account.csv", accounts,
              ["account_id", "entity_id", "account_number", "program_id", "available_balance", "bank_code"])
    write_csv(f"{OUT}/transaction.csv", txns,
              ["transaction_id", "account_id", "transaction_date", "transaction_type",
               "description", "transaction_amount", "transaction_reference_id", "utr_number"])

    os.makedirs(f"{OUT}/truth", exist_ok=True)
    write_csv(f"{OUT}/truth/transaction_truth.csv", truth,
              ["transaction_id", "counterparty_id", "counterparty_name", "rail", "category",
               "loan_ref", "recon_status", "recon_match_id", "is_anomaly"])
    write_csv(f"{OUT}/truth/counterparty.csv",
              [{"counterparty_id": c["counterparty_id"], "canonical_name": c["canonical_name"],
                "category": c["category"], "aliases": " | ".join(c["aliases"])} for c in cps],
              ["counterparty_id", "canonical_name", "category", "aliases"])
    write_csv(f"{OUT}/truth/entity.csv",
              [{"entity_id": e[0], "entity_name": e[1]} for e in entities],
              ["entity_id", "entity_name"])

    write_mysql(f"{OUT}/seed_mysql.sql", BANKS, accounts, txns)
    if not a.no_load:
        print("loading into MySQL ...", flush=True)
        load_mysql(BANKS, accounts, txns)

    print("done.")
    for n in dirty_notes:
        print("  dirty:", n)

if __name__ == "__main__":
    main()
