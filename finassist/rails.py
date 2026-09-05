"""finassist/rails.py — turn a bank narration string into structured fields.

`description` is the only place a counterparty name exists in the TBX schema. There is no
vendor table, no category, no reconciliation status. Everything downstream is derived here.

Nine narration formats appear in the data and the counterparty sits in a DIFFERENT position
in each one, so this is one parser per rail, never one regex. Parsing happens ONCE at
ingestion; the query layer then does an ordinary GROUP BY on a real column.

    parse("NEFT/970816397610/ICIC/pooja reddy")
    -> Parsed(rail='NEFT_SLASH', counterparty_raw='POOJA REDDY', external_ref='970816397610', ...)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from finassist.aliases import ACRONYMS

# A Bajaj Finance loan/obligation reference: ZBFLCTP + 3 alphanumerics + PBL + 8 digits.
# When one of these is present the credit is an inward collection against a known loan,
# which is the strongest reconciliation signal in the dataset.
LOAN_REF = re.compile(r"\bZBFLCTP[0-9A-Z]{3}PBL\d{8}\b")

# Trailing branch/scheme codes that ride along after a counterparty name, e.g.
# "UMANG SELECTIONHAPURBPES DPF10129". Stripped so they do not fragment a vendor.
TRAILING_CODE = re.compile(r"\s+[A-Z]{2,4}\d{4,6}$")

# Location suffixes are appended after a double space on FT rails:
#   "SELECTION ELECTRONICS   DAHISAR EAST"
# Two of the real locations contain the word SELECT ("SELECT CITY SAKET DELHI",
# "SELECT CITYWALK DELHI"), so a naive substring search for a vendor called "Select"
# hits a shopping mall. Splitting the location off before matching is what prevents that.
LOCATION_SPLIT = re.compile(r"\s{2,}")

CHARGE_WORDS = ("charges", "charge", "fee", "deposits", "amc", "sms alert")


@dataclass(frozen=True)
class Parsed:
    rail: str
    counterparty_raw: str | None      # name exactly as it appeared, upper-cased
    location: str | None              # branch/city suffix, split off so it can't pollute the name
    external_ref: str | None          # the rail's own reference (UPI ref, NEFT ref, ...)
    loan_ref: str | None              # ZBFLCTP...PBL######## if present
    counter_account: str | None       # the other side's account number, when the rail carries it

    def as_dict(self) -> dict:
        return asdict(self)


def _clean(name: str | None) -> str | None:
    """Normalise a raw name fragment: collapse whitespace, drop trailing branch codes, upper-case.
    Case is folded because the same person appears as 'ritu verma', 'Ritu Verma' and 'RITU VERMA'."""
    if not name:
        return None
    n = " ".join(name.split()).strip(" -/,.")
    n = TRAILING_CODE.sub("", n)
    return n.upper() or None


def _split_location(tail: str) -> tuple[str | None, str | None]:
    """FT rails append a location after 2+ spaces. Return (name, location)."""
    parts = LOCATION_SPLIT.split(tail.strip(), maxsplit=1)
    name = parts[0] if parts else None
    loc = parts[1] if len(parts) > 1 else None
    return _clean(name), _clean(loc)


def _field(parts: list[str], i: int) -> str | None:
    return parts[i] if 0 <= i < len(parts) else None


def parse(description: str | None) -> Parsed:
    """Classify one narration and pull the counterparty out of it.

    Unknown formats return rail='UNKNOWN' with counterparty_raw=None rather than a guess.
    A guessed vendor is worse than no vendor: it silently corrupts a total."""
    d = (description or "").strip()
    if not d:
        return Parsed("UNKNOWN", None, None, None, None, None)

    loan = m.group(0) if (m := LOAN_REF.search(d)) else None

    # --- bank charges: no counterparty at all -------------------------------
    low = d.lower()
    if len(d) < 60 and any(w in low for w in CHARGE_WORDS) and "/" not in d and " - " not in d:
        return Parsed("CHARGES", None, None, None, None, None)

    # --- FT -  <ref> -  <acct> - <NAME>   <LOCATION> -------------------------
    if d.startswith("FT -  "):
        p = [x.strip() for x in d.split(" - ")]
        name, loc = _split_location(p[-1]) if len(p) >= 2 else (None, None)
        return Parsed("FT_SPACED", name, loc, _field(p, 1), loan, _field(p, 2))

    # --- FT-<REF>-<NAME>   <LOCATION> ----------------------------------------
    if d.startswith("FT-"):
        p = d[3:].split("-", 1)
        name, loc = _split_location(p[1]) if len(p) > 1 else (None, None)
        return Parsed("FT_COMPACT", name, loc, _clean(_field(p, 0)), loan, None)

    # --- UPI-<PAYEE>-XXXXXX####-<IFSC>-<ref>-<stamp> -------------------------
    # Counterparty is the SECOND field here, not the last.
    if d.startswith("UPI-"):
        p = d.split("-")
        return Parsed("UPI", _clean(_field(p, 1)), None, _field(p, 4), loan, None)

    # --- NEFT  - <IFSC> - <ref> - <acct> - <NAME> ----------------------------
    if d.startswith("NEFT  - "):
        p = [x.strip() for x in d.split(" - ")]
        return Parsed("NEFT_SPACED", _clean(p[-1]), None, _field(p, 2), loan, _field(p, 3))

    # --- NEFT/<ref>/<BANK>/<NAME> --------------------------------------------
    if d.startswith("NEFT/"):
        p = d.split("/")
        return Parsed("NEFT_SLASH", _clean(p[-1]), None, _field(p, 1), loan, None)

    # --- RTGS  - <IFSC> - <ref> - <acct> - <NAME> ----------------------------
    if d.startswith("RTGS  - ") or d.startswith("RTGS - "):
        p = [x.strip() for x in d.split(" - ")]
        return Parsed("RTGS", _clean(p[-1]), None, _field(p, 2), loan, _field(p, 3))

    # --- IMPS OW/<ref>/<Name>/<BANK>/<acct> ----------------------------------
    # Counterparty is the THIRD field.
    if d.startswith("IMPS OW/"):
        p = d.split("/")
        return Parsed("IMPS_OW", _clean(_field(p, 2)), None, _field(p, 1), loan, _field(p, 4))

    # --- IMPS/P2A/<ref>/<BANK>/<acct>/00/INET/<code>/<PAYEE>/<loan>/INWD## ----
    # Counterparty is the NINTH field.
    if d.startswith("IMPS/P2A/"):
        p = d.split("/")
        return Parsed("IMPS_P2A", _clean(_field(p, 8)), None, _field(p, 2), loan, _field(p, 4))

    # --- R/<rblref>/<loanref>//<NAME>/<rblref> /<NAME>  (RBL inward) ---------
    # Note the empty field produced by the '//'.
    if d.startswith("R/"):
        p = d.split("/")
        return Parsed("RBL_INWARD", _clean(_field(p, 4)), None, _field(p, 1), loan, None)

    # --- unrecognised: say so, do not guess ----------------------------------
    return Parsed("UNKNOWN", None, None, None, loan, None)


# --------------------------------------------------------------------------
# Canonicalisation: many spellings -> one counterparty
# --------------------------------------------------------------------------

# Corporate suffixes carry no identity. "INFOSYS LTD" and "INFOSYS LIMITED" are one vendor.
# Tokens that carry no identity. Dropped wherever they appear, not only at the end:
# "GOOGLE INDIA PVT LTD ADS" and "GOOGLE INDIA ADS" are one vendor, and the corporate
# words sit in the MIDDLE of the first one. Trailing-only stripping misses that.
STOPWORDS = {
    "PRIVATE", "PVT", "LIMITED", "LTD", "LLP", "PTE", "PTY", "INC",
    "CORP", "CORPORATION", "COMPANY", "AND", "INDIA", "DEPARTMENT", "THE", "OF",
}
NOISE = re.compile(r"[^A-Z0-9 ]+")


def canon_key(name: str | None) -> str | None:
    """A comparison key for grouping spellings of one vendor.

    Deliberately conservative. It removes punctuation, corporate suffixes and spacing, so
    'RELIANCEDIGITAL RETAIL LTD' and 'RELIANCE DIGITAL RETAIL LIMITED' collapse together.
    It does NOT do fuzzy or substring matching, because 'SELECTION MOBILE' and
    'SELECTRICITY TWO' are different businesses and any substring rule merges them.
    """
    if not name:
        return None
    n = NOISE.sub(" ", name.upper())
    toks = [w for w in n.split() if w not in STOPWORDS]
    if not toks:                          # a name made only of stopwords keeps its raw form
        toks = n.split()
    key = "".join(toks) or None
    return ACRONYMS.get(key, key)


# A shorter key may be an abbreviation of a longer one: RAZORPAY / RAZORPAYSOFTWARE,
# JIOINFOCOMM / RELIANCEJIOINFOCOMM, SELECTIONMOBILE / SELECTIONMOBILESHOP.
# Containment at a word boundary is the only merge rule used, and only when the shorter key
# is long enough to be distinctive. Substring matching in general is what wrongly fuses
# SELECTION MOBILE with SELECTRICITY TWO, so the length floors below are the safety margin:
# a prefix must share at least MIN_PREFIX characters, a suffix at least MIN_SUFFIX.
MIN_PREFIX, MIN_SUFFIX = 8, 10


def _depluralise(k: str) -> str:
    """MOBILES -> MOBILE. Only a trailing 's', and never on a short key."""
    return k[:-1] if len(k) > MIN_PREFIX and k.endswith("S") and not k.endswith("SS") else k


def merge_keys(keys):
    """Group canonical keys that are abbreviations of one another.

    Returns {key: group_key}, where group_key is the longest member of the group. Uses
    union-find so a chain (RAZORPAY -> RAZORPAYSOFTWARE -> RAZORPAYSOFTWAREINDIA) lands in
    one group rather than a pair of overlapping ones.
    """
    keys = sorted(set(k for k in keys if k))
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb, key=len)] = min(ra, rb, key=len)

    norm = {k: _depluralise(k) for k in keys}
    for i, a in enumerate(keys):
        na = norm[a]
        for b in keys[i + 1:]:
            nb = norm[b]
            short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
            if short == long_:
                union(a, b)
            elif len(short) >= MIN_PREFIX and long_.startswith(short):
                union(a, b)
            elif len(short) >= MIN_SUFFIX and long_.endswith(short):
                union(a, b)

    groups = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    return {k: max(members, key=len) for root, members in groups.items() for k in members}
