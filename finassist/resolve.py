"""finassist/resolve.py — turn words into rows, deterministically.

Two jobs, both done in code rather than by the model:

  resolve_period()       "last month" -> ('2026-08-01', '2026-08-31')
  resolve_counterparty() "blue dart"  -> one counterparty row, or a list to disambiguate

Neither uses the language model. A date range and a vendor id are facts about the data,
and a model that guesses either produces a confidently wrong total.

The clock is the DATA, not the wall. `anchor()` returns the latest transaction date in the
database, and every relative period is measured from that. If the wall clock were used
instead, "last month" against a dataset that ends in June would return zero rows and the
assistant would report a spend of zero rather than saying the period is empty.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from finassist import db as _db

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})


@dataclass
class Period:
    start: str
    end: str
    label: str

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "label": self.label}


@dataclass
class Resolution:
    """Either resolved (`value` set), ambiguous (`candidates` set), or missing (both empty)."""
    kind: str
    value: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    query: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None

    @property
    def ambiguous(self) -> bool:
        return self.value is None and len(self.candidates) > 1

    @property
    def missing(self) -> bool:
        return self.value is None and not self.candidates


def _to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def anchor(cx: _db.Connection) -> date:
    """The most recent transaction date. This is 'today' for every relative period."""
    row = cx.execute("SELECT MAX(txn_date) FROM transaction_enriched").fetchone()
    return _to_date(row[0]) or date.today()


def coverage(cx: _db.Connection) -> tuple[str, str]:
    r = cx.execute("SELECT MIN(txn_date), MAX(txn_date) FROM transaction_enriched").fetchone()
    return (str(r[0])[:10], str(r[1])[:10]) if r and r[0] else ("", "")


def _month_bounds(y: int, m: int) -> tuple[str, str]:
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def _shift_month(d: date, months: int) -> tuple[int, int]:
    t = d.year * 12 + (d.month - 1) + months
    return t // 12, t % 12 + 1


def _quarter_bounds(y: int, q: int) -> tuple[str, str]:
    sm = 3 * (q - 1) + 1
    return f"{y}-{sm:02d}-01", f"{y}-{sm+2:02d}-{calendar.monthrange(y, sm+2)[1]:02d}"


def resolve_period(text: str, today: date) -> Period | None:
    """Parse a period out of free text. Returns None when the text names no period,
    which the caller treats as 'the whole dataset', never as 'guess one'."""
    t = (text or "").lower()

    # explicit ISO range
    if m := re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:to|and|-|until|through)\s*(\d{4}-\d{2}-\d{2})", t):
        return Period(m.group(1), m.group(2), f"{m.group(1)} to {m.group(2)}")

    # Natural-language month range: "January 2026 till July 2026" or
    # "Jan through Jul 2026". Resolve both endpoints before the single-month parser.
    month_names = "|".join(re.escape(name) for name in sorted(MONTHS, key=len, reverse=True))
    if m := re.search(
            rf"\b({month_names})\s+(\d{{4}})\s*(?:to|through|till|until|and|-)\s*"
            rf"({month_names})(?:\s+(\d{{4}}))?\b", t):
        start_name, start_year, end_name, end_year = m.groups()
        sy, sm = int(start_year), MONTHS[start_name.lower()]
        ey, em = int(end_year or start_year), MONTHS[end_name.lower()]
        start, _ = _month_bounds(sy, sm)
        _, end = _month_bounds(ey, em)
        return Period(start, end, f"{calendar.month_name[sm]} {sy} to {calendar.month_name[em]} {ey}")

    # explicit ISO month
    if m := re.search(r"\b(\d{4})-(\d{2})\b", t):
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            s, e = _month_bounds(y, mo)
            return Period(s, e, f"{calendar.month_name[mo]} {y}")

    if "year to date" in t or "ytd" in t:
        return Period(f"{today.year}-01-01", today.isoformat(), f"year to date {today.year}")

    if m := re.search(r"last\s+(\d{1,4})\s+days?", t):
        n = int(m.group(1))
        return Period((today - timedelta(days=n - 1)).isoformat(), today.isoformat(), f"last {n} days")

    if m := re.search(r"last\s+(\d{1,3})\s+months?", t):
        n = int(m.group(1))
        y, mo = _shift_month(today, -n)
        s, _ = _month_bounds(y, mo)
        return Period(s, today.isoformat(), f"last {n} months")

    if "last month" in t or "prev month" in t or "previous month" in t or "prior month" in t:
        y, mo = _shift_month(today, -1)
        s, e = _month_bounds(y, mo)
        return Period(s, e, f"{calendar.month_name[mo]} {y}")

    if "this month" in t or "current month" in t:
        s, e = _month_bounds(today.year, today.month)
        return Period(s, min(e, today.isoformat()), f"{calendar.month_name[today.month]} {today.year}")

    if "last quarter" in t or "previous quarter" in t:
        q = (today.month - 1) // 3 + 1
        y, q = (today.year - 1, 4) if q == 1 else (today.year, q - 1)
        s, e = _quarter_bounds(y, q)
        return Period(s, e, f"Q{q} {y}")

    if "this quarter" in t:
        q = (today.month - 1) // 3 + 1
        s, e = _quarter_bounds(today.year, q)
        return Period(s, min(e, today.isoformat()), f"Q{q} {today.year}")

    if m := re.search(r"\bq([1-4])\s*(?:of\s*)?(\d{4})?", t):
        q = int(m.group(1)); y = int(m.group(2)) if m.group(2) else today.year
        s, e = _quarter_bounds(y, q)
        return Period(s, e, f"Q{q} {y}")

    if "last year" in t or "previous year" in t:
        y = today.year - 1
        return Period(f"{y}-01-01", f"{y}-12-31", str(y))

    if "this year" in t:
        return Period(f"{today.year}-01-01", today.isoformat(), str(today.year))

    # "in August", "in August 2026", "for March 2026"
    for name, num in MONTHS.items():
        if re.search(rf"\b{name}\b", t):
            ym = re.search(rf"\b{name}\b\s*(\d{{4}})", t)
            y = int(ym.group(1)) if ym else today.year
            # a bare month name that has not happened yet this year means last year
            if not ym and (y, num) > (today.year, today.month):
                y -= 1
            s, e = _month_bounds(y, num)
            return Period(s, e, f"{calendar.month_name[num]} {y}")

    if m := re.search(r"\b(20\d{2})\b", t):
        y = int(m.group(1))
        return Period(f"{y}-01-01", f"{y}-12-31", str(y))

    return None


def previous_period(p: Period) -> Period:
    """The comparable period immediately before `p`, same length. Used for 'compared to
    the month before' without the model having to work out any dates."""
    s, e = date.fromisoformat(p.start), date.fromisoformat(p.end)
    if s.day == 1 and e.day == calendar.monthrange(e.year, e.month)[1] and s.month == e.month:
        y, m = _shift_month(s, -1)                       # a whole calendar month
        ps, pe = _month_bounds(y, m)
        return Period(ps, pe, f"{calendar.month_name[m]} {y}")
    span = (e - s).days + 1
    pe = s - timedelta(days=1)
    ps = pe - timedelta(days=span - 1)
    return Period(ps.isoformat(), pe.isoformat(), f"{ps.isoformat()} to {pe.isoformat()}")


# --------------------------------------------------------------------------
# Counterparty resolution
# --------------------------------------------------------------------------

_STOP = {"the", "a", "an", "to", "for", "on", "in", "of", "we", "did", "how", "much",
         "spend", "spent", "pay", "paid", "payment", "payments", "last", "month", "year",
         "quarter", "total", "with", "at", "our", "us", "and", "vendor", "supplier"}


def resolve_counterparty(cx: _db.Connection, text: str, limit: int = 8) -> Resolution:
    """Find the counterparty a user named.

    Matching is layered, strictest first, and STOPS at the first layer that produces
    exactly one hit. A layer that produces several hits returns them as candidates for the
    user to choose between -- it never picks the biggest one. Silently choosing among
    'SELECTION MOBILE', 'SELECT MOBILES' and 'SELECTRICITY TWO' is how a finance assistant
    reports a confident wrong number.
    """
    from finassist.rails import canon_key

    q = (text or "").strip()
    if not q:
        return Resolution("counterparty", query=q)

    rows = lambda sql, *a: [dict(r) for r in cx.execute(sql, a)]
    key = canon_key(q)

    # 1. exact canonical key
    if key:
        hit = rows("SELECT * FROM counterparty WHERE canon_key = ?", key)
        if len(hit) == 1:
            return Resolution("counterparty", value=hit[0], query=q)

    # 2. exact display name, case-insensitive
    hit = rows("SELECT * FROM counterparty WHERE UPPER(canonical_name) = UPPER(?)", q)
    if len(hit) == 1:
        return Resolution("counterparty", value=hit[0], query=q)

    # 3. prefix of the canonical key
    prefix_hits = []
    if key and len(key) >= 4:
        prefix_hits = rows("SELECT * FROM counterparty WHERE canon_key LIKE ? ORDER BY txn_count DESC",
                           key + "%")
        if len(prefix_hits) == 1:
            return Resolution("counterparty", value=prefix_hits[0], query=q)

    # 4. every significant word must appear somewhere in the name (AND, not OR)
    word_hits = []
    words = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if w not in _STOP and len(w) > 2]
    if words:
        clause = " AND ".join("canon_key LIKE ?" for _ in words)
        word_hits = rows(f"SELECT * FROM counterparty WHERE {clause} ORDER BY txn_count DESC",
                         *[f"%{w.upper()}%" for w in words])
        if not prefix_hits and len(word_hits) == 1:
            return Resolution("counterparty", value=word_hits[0], query=q)

    # Ambiguous: show EVERY plausible match, not just the strictest layer's. A user asking
    # about "Selection" needs to see all six colliding vendors to pick one; showing three
    # of them invites them to assume the other three do not exist.
    seen, merged = set(), []
    for r in prefix_hits + word_hits:
        if r["counterparty_id"] not in seen:
            seen.add(r["counterparty_id"]); merged.append(r)
    if len(merged) == 1:
        return Resolution("counterparty", value=merged[0], query=q)
    if merged:
        merged.sort(key=lambda r: -r["txn_count"])
        return Resolution("counterparty", candidates=merged[:limit], query=q)

    return Resolution("counterparty", query=q)
