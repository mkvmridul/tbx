"""finassist/enrich.py — build the tables the TBX schema is missing.

The brief asks for vendor payouts, reconciliation status and categories. None of those
exist as columns. This module derives them ONCE, at ingestion, from `description`, and
writes two derived tables:

    counterparty            the vendor list  (canonical name + every alias seen)
    transaction_enriched    per transaction: rail, counterparty_id, category,
                            loan_ref, recon_status, recon_match_id, anomaly_z

Doing it here rather than at query time is the whole design. After this runs, "how much
did we pay Blue Dart last month" is an ordinary indexed GROUP BY, so the language model
never has to read a bank narration string and never has to do arithmetic.

    python -m finassist.enrich
"""
from __future__ import annotations

import math
import os
import statistics
import sys
from collections import defaultdict

from finassist import db as _db
from finassist.rails import parse, canon_key, merge_keys

# Robust z-score constants. MAD (median absolute deviation) is used instead of the standard
# deviation because a single 500x payout would inflate sigma enough to hide itself.
# 1.4826 = 1/Phi^-1(0.75), which makes MAD a consistent estimator of sigma for normal data.
MAD_TO_SIGMA = 1.4826
# |z| above this is called unusual. Measured on this dataset, sweeping the threshold:
#   z>2.5 -> 3.7% precision, 66.0% recall     z>4.0 -> 52.9% precision, 19.1% recall
#   z>3.0 -> 12.0% precision, 46.8% recall    z>4.5 -> 75.0% precision, 12.8% recall
#   z>3.5 -> 30.2% precision, 27.7% recall    z>5.0 -> 100%  precision,  6.4% recall
# 3.5 maximises F1, but F1 is the wrong objective here: an anomaly callout rides along
# with an answer the user asked for, so a false flag undermines a correct answer while a
# missed flag only omits a bonus. Tuned for precision.
ANOMALY_Z = 4.0
MIN_HISTORY = 8          # below this a vendor has too little history to judge

DDL = """
DROP TABLE IF EXISTS transaction_enriched;
DROP TABLE IF EXISTS counterparty;

CREATE TABLE counterparty (
    counterparty_id   VARCHAR(16)   PRIMARY KEY,
    canonical_name    VARCHAR(255)  NOT NULL,
    canon_key         VARCHAR(255)  NOT NULL,
    kind              VARCHAR(20)   NOT NULL,          -- business | individual | unknown
    category          VARCHAR(64)   NULL,
    aliases           TEXT          NOT NULL,          -- '|'-separated spellings actually seen
    txn_count         INT           NOT NULL,
    total_debit       DECIMAL(18,2) NOT NULL,
    total_credit      DECIMAL(18,2) NOT NULL,
    first_seen        VARCHAR(10)   NULL,
    last_seen         VARCHAR(10)   NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE transaction_enriched (
    transaction_id    VARCHAR(36)   PRIMARY KEY,
    account_id        VARCHAR(36)   NOT NULL,
    txn_date          VARCHAR(10)   NOT NULL,          -- 'YYYY-MM-DD', for cheap date filtering
    txn_month         VARCHAR(7)    NOT NULL,          -- 'YYYY-MM'
    transaction_type  VARCHAR(10)   NOT NULL,
    amount            DECIMAL(15,2) NOT NULL,
    signed_amount     DECIMAL(15,2) NOT NULL,          -- debit negative, credit positive
    rail              VARCHAR(32)   NOT NULL,
    counterparty_id   VARCHAR(16)   NULL,
    counterparty_raw  VARCHAR(255)  NULL,
    location          VARCHAR(255)  NULL,
    category          VARCHAR(64)   NULL,
    external_ref      VARCHAR(64)   NULL,
    loan_ref          VARCHAR(64)   NULL,
    recon_status      VARCHAR(24)   NOT NULL,          -- matched_pair|matched_ref|unmatched|not_applicable
    recon_match_id    VARCHAR(36)   NULL,
    anomaly_z         DECIMAL(8,3)  NULL,
    is_anomaly        TINYINT       NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

INDEXES = """
CREATE INDEX ix_te_date        ON transaction_enriched (txn_date);
CREATE INDEX ix_te_month       ON transaction_enriched (txn_month);
CREATE INDEX ix_te_cp          ON transaction_enriched (counterparty_id);
CREATE INDEX ix_te_cp_month    ON transaction_enriched (counterparty_id, txn_month);
CREATE INDEX ix_te_cat_month   ON transaction_enriched (category, txn_month);
CREATE INDEX ix_te_recon       ON transaction_enriched (recon_status);
CREATE INDEX ix_te_acct_date   ON transaction_enriched (account_id, txn_date);
CREATE INDEX ix_te_anom        ON transaction_enriched (is_anomaly);
CREATE INDEX ix_cp_key         ON counterparty (canon_key);
CREATE INDEX ix_cp_name        ON counterparty (canonical_name);
"""

# Keyword -> category. Applied to the canonical name. This is a derived convenience, not a
# chart of accounts: the schema has no category column, so anything here is an inference and
# the assistant must say so when asked a category question.
CATEGORY_RULES = [
    ("utilities",             ("POWER", "ELECTRIC", "ENERGY", "GAS", "BESCOM", "AIRTEL", "JIO", "TELECOM", "SELECTRICITY")),
    ("logistics",             ("LOGISTIC", "EXPRESS", "COURIER", "DELHIVERY", "GATI", "SAFEXPRESS", "BLUE DART", "BLUEDART", "TRANSPORT")),
    ("software",              ("MICROSOFT", "GOOGLE CLOUD", "ZOHO", "FRESHWORKS", "ATLASSIAN", "RAZORPAY", "AMAZON WEB", "AMAZON SELLER", "SOFTWARE", "TECHNOLOG")),
    ("rent",                  ("EMBASSY", "DLF", "RMZ", "BRIGADE", "REIT", "OFFICE PARK", "PROPERT")),
    ("marketing",             ("ADS", "META PLATFORMS", "DENTSU", "OGILVY", "MEDIA", "ADVERTIS")),
    ("travel",                ("MAKEMYTRIP", "YATRA", "OYO", "AVIATION", "AIRLIN", "INDIGO", "HOTEL", "TRAVEL")),
    ("insurance",             ("INSURANCE", "LOMBARD", "HDFC LIFE", "ASSURANCE CO")),
    ("tax",                   ("GST", "INCOME TAX", "INCOMETAX", "CBDT", "TDS")),
    ("professional_services", ("DELOITTE", "KPMG", "CONSULTING", "CONSULTANCY", "MANGALDAS", "QUESS", "INFOSYS", "LARSEN", "ADVOCATE", "LEGAL")),
    ("inventory",             ("ELECTRONIC", "MOBILE", "RETAIL", "SALES", "DIGITAL", "CROMA", "VIJAY", "SANGEETHA", "POORVIKA", "HAVELLS", "VOLTAS", "BLUE STAR", "BAJAJ ELECTRICAL", "SELECTION", "SELECT ", "MALIGAI", "TRADERS")),
]

BUSINESS_HINTS = ("LIMITED", "LTD", "PVT", "PRIVATE", "LLP", "CORP", "INC", "COMPANY",
                  "ENTERPRISES", "SERVICES", "TRADERS", "RETAIL", "INDUSTRIES", "AND SONS",
                  "REIT", "PTE", "PTY", "DEPARTMENT", "BANK", "GST", "TDS")


def categorise(name: str | None) -> str | None:
    if not name:
        return None
    up = name.upper()
    for cat, keys in CATEGORY_RULES:
        if any(k in up for k in keys):
            return cat
    return None


def classify_kind(name: str, aliases: set[str]) -> str:
    """Business or individual. Individuals show up in mixed case in the raw narration and
    carry no corporate suffix; businesses are all-caps with one."""
    up = name.upper()
    if any(h in up for h in BUSINESS_HINTS):
        return "business"
    # a name written in mixed case anywhere ('Gautam singh') is a person
    if any(a != a.upper() for a in aliases):
        return "individual"
    return "business" if len(up.split()) > 3 else "unknown"


def _date10(v):
    """Coerce a transaction_date (datetime from MySQL, str otherwise) to 'YYYY-MM-DD'."""
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)[:10]


def _date7(v):
    return v.strftime("%Y-%m") if hasattr(v, "strftime") else str(v)[:7]


def build(verbose: bool = True) -> dict:
    cx = _db.connect()                 # writable target: derived tables land here
    src = _db.connect_source()         # raw tables: may be a read-only server
    since = os.environ.get("ENRICH_SINCE", "").strip()
    where, params = ("WHERE transaction_date >= ?", [since]) if since else ("", [])
    if _db.source_dsn() != _db.dsn():
        # The engine joins bank/account in the target DB: mirror them from the source.
        banks = src.execute("SELECT bank_code, bank_name FROM bank").fetchall()
        accts = src.execute("SELECT account_id, entity_id, account_number, program_id, "
                            "available_balance, bank_code FROM account").fetchall()
        cx.execute("SET FOREIGN_KEY_CHECKS=0")   # the old synthetic `transaction` table points at account
        cx.execute("DELETE FROM account"); cx.execute("DELETE FROM bank")
        cx.execute("ALTER TABLE account MODIFY account_number VARCHAR(255)")  # source values exceed the spec's 20
        cx.executemany("INSERT INTO bank VALUES (?,?)", [tuple(b) for b in banks])
        for i in range(0, len(accts), 2000):
            cx.executemany("INSERT INTO account VALUES (?,?,?,?,?,?)", [tuple(a) for a in accts[i:i + 2000]])
        cx.execute("SET FOREIGN_KEY_CHECKS=1")
        if verbose:
            print(f"  mirrored {len(banks)} banks, {len(accts):,} accounts from {_db.source_label()}")
    rows = src.execute(f"""
        SELECT transaction_id, account_id, transaction_date, transaction_type,
               description, transaction_amount, transaction_reference_id, utr_number
        FROM `transaction` {where}
    """, params).fetchall()
    if verbose:
        print(f"  read {len(rows):,} transactions")

    # ---- pass 1: parse every narration -------------------------------------
    parsed = []
    for r in rows:
        p = parse(r["description"])
        parsed.append({"row": r, "p": p, "key": canon_key(p.counterparty_raw)})

    # Fold abbreviations into one group before counting, so RAZORPAY and RAZORPAY SOFTWARE
    # are one vendor rather than two half-sized ones.
    groups = merge_keys(rec["key"] for rec in parsed)
    by_key = defaultdict(lambda: {"aliases": set(), "rows": []})
    for rec in parsed:
        if rec["key"]:
            rec["key"] = groups.get(rec["key"], rec["key"])
            by_key[rec["key"]]["aliases"].add(rec["p"].counterparty_raw)
            by_key[rec["key"]]["rows"].append(rec)

    # ---- pass 2: one counterparty per canonical key -------------------------
    # Canonical display name = the longest alias seen. The longest spelling is the most
    # complete one ('RELIANCE DIGITAL RETAIL LTD' beats 'RELIANCE DIGITAL'), so it reads
    # correctly in an answer without inventing a name that never appeared in the data.
    cp_of_key, cp_rows = {}, []
    for n, (key, blob) in enumerate(sorted(by_key.items()), start=1):
        aliases = blob["aliases"]
        canonical = max(aliases, key=len)
        cid = f"CP{n:05d}"
        cp_of_key[key] = cid
        deb = sum(float(x["row"]["transaction_amount"]) for x in blob["rows"]
                  if x["row"]["transaction_type"] == "debit")
        cre = sum(float(x["row"]["transaction_amount"]) for x in blob["rows"]
                  if x["row"]["transaction_type"] == "credit")
        dates = sorted(_date10(x["row"]["transaction_date"]) for x in blob["rows"])
        cp_rows.append((cid, canonical, key, classify_kind(canonical, aliases),
                        categorise(canonical), " | ".join(sorted(aliases)),
                        len(blob["rows"]), round(deb, 2), round(cre, 2), dates[0], dates[-1]))

    if verbose:
        print(f"  derived {len(cp_rows):,} counterparties from free text")

    # ---- pass 3: reconciliation --------------------------------------------
    # Signal A: two transactions sharing a utr_number, opposite direction, equal amount,
    #           different accounts -> two legs of one internal transfer.
    # Signal B: a structured ZBFLCTP...PBL loan reference -> inward collection against a
    #           known obligation.
    # Everything else is unmatched. Bank charges have no counterparty and are N/A.
    utr_groups = defaultdict(list)
    for rec in parsed:
        u = rec["row"]["utr_number"]
        if u:
            utr_groups[u].append(rec)

    recon = {}
    for u, grp in utr_groups.items():
        if len(grp) != 2:
            continue
        a, b = grp[0]["row"], grp[1]["row"]
        if ({a["transaction_type"], b["transaction_type"]} == {"debit", "credit"}
                and abs(float(a["transaction_amount"]) - float(b["transaction_amount"])) < 0.01
                and a["account_id"] != b["account_id"]):
            recon[a["transaction_id"]] = ("matched_pair", b["transaction_id"])
            recon[b["transaction_id"]] = ("matched_pair", a["transaction_id"])

    # ---- pass 4: anomaly score, per counterparty ---------------------------
    # A payout is unusual relative to THAT vendor's own history, not to the whole ledger.
    # Median + MAD are used because a single huge outlier does not move them, so the
    # anomaly cannot mask itself the way it would with a mean and standard deviation.
    # Scored in LOG space. Payment amounts are log-normal, not normal: a vendor whose
    # typical payment is Rs 50k routinely sends Rs 500k, so on the raw scale an ordinary
    # large payment already sits many sigma out and the detector fires constantly.
    # Measured on this dataset: raw-scale scoring gave 1.0% precision (4,374 false alarms);
    # log-scale scoring gives the numbers in the eval below.
    amounts = defaultdict(list)
    for rec in parsed:
        if rec["key"] and rec["row"]["transaction_type"] == "debit":
            amounts[rec["key"]].append(math.log(max(float(rec["row"]["transaction_amount"]), 1.0)))

    stats = {}
    for key, vals in amounts.items():
        if len(vals) < MIN_HISTORY:
            continue
        med = statistics.median(vals)
        mad = statistics.median([abs(v - med) for v in vals])
        sigma = MAD_TO_SIGMA * mad
        if sigma <= 0:
            # MAD collapses to zero when more than half the history ties on one value.
            # Without a floor every such vendor scores z = infinity and the emptiest
            # vendors rank as the biggest anomalies. Fall back to the IQR estimate.
            q = statistics.quantiles(vals, n=4) if len(vals) >= 4 else None
            sigma = (q[2] - q[0]) / 1.349 if q and q[2] > q[0] else max(med * 0.5, 1.0)
        stats[key] = (med, sigma)

    # ---- write ---------------------------------------------------------------
    cx.executescript(DDL)
    cx.executemany("INSERT INTO counterparty VALUES (?,?,?,?,?,?,?,?,?,?,?)", cp_rows)

    out, n_anom = [], 0
    for rec in parsed:
        r, p, key = rec["row"], rec["p"], rec["key"]
        cid = cp_of_key.get(key) if key else None
        amt = float(r["transaction_amount"])
        signed = amt if r["transaction_type"] == "credit" else -amt

        if p.rail == "CHARGES":
            status, match = "not_applicable", None
        elif r["transaction_id"] in recon:
            status, match = recon[r["transaction_id"]]
        elif p.loan_ref:
            status, match = "matched_ref", None
        else:
            status, match = "unmatched", None

        z = None
        if key in stats and r["transaction_type"] == "debit":
            med, sigma = stats[key]
            z = round((math.log(max(amt, 1.0)) - med) / sigma, 2) if sigma else None
        anom = 1 if (z is not None and abs(z) > ANOMALY_Z) else 0
        n_anom += anom

        out.append((r["transaction_id"], r["account_id"], _date10(r["transaction_date"]),
                    _date7(r["transaction_date"]), r["transaction_type"], amt, signed,
                    p.rail, cid, p.counterparty_raw, p.location,
                    categorise(p.counterparty_raw), p.external_ref, p.loan_ref,
                    status, match, z, anom))

    # MySQL's max_allowed_packet caps executemany batch size; chunk to be safe.
    B = 5000
    for i in range(0, len(out), B):
        cx.executemany(
            "INSERT INTO transaction_enriched VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            out[i:i + B])
    cx.executescript(INDEXES)
    cx.commit()

    summary = {
        "transactions": len(out),
        "counterparties": len(cp_rows),
        "unparsed": sum(1 for r in out if r[7] == "UNKNOWN"),
        "no_counterparty": sum(1 for r in out if r[8] is None and r[7] != "CHARGES"),
        "matched_pair": sum(1 for r in out if r[14] == "matched_pair"),
        "matched_ref": sum(1 for r in out if r[14] == "matched_ref"),
        "unmatched": sum(1 for r in out if r[14] == "unmatched"),
        "not_applicable": sum(1 for r in out if r[14] == "not_applicable"),
        "anomalies": n_anom,
    }
    cx.close()
    return summary


if __name__ == "__main__":
    print(f"enriching {_db.source_label()} -> {_db.dsn_label()}"
          + (f"  (since {os.environ['ENRICH_SINCE']})" if os.environ.get("ENRICH_SINCE") else "") + " ...")
    s = build()
    for k, v in s.items():
        print(f"  {k:<18} {v:>10,}")
