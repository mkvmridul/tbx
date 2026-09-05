"""finassist/facts.py — whole-dataset facts computed ON THE SOURCE DATABASE and cached locally.

The hackathon server holds 10M rows and answers a money query in ~2 minutes, so the engine
works from a local window. But "how many transactions are there?" deserves the source's own
number, not the window's. This module runs a handful of cheap COUNT/MIN/MAX queries on the
source, caches them in data/source_facts.json, and answers matching questions from that cache
with the SQL that produced each figure.

    python -m finassist.facts      # refresh the cache from the source database
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

from finassist import db as _db

PATH = _db._ROOT / "data" / "source_facts.json"

QUERIES = {
    "transactions": "SELECT COUNT(*) FROM `transaction`",
    "accounts":     "SELECT COUNT(*) FROM account",
    "banks":        "SELECT COUNT(*) FROM bank",
    "entities":     "SELECT COUNT(DISTINCT entity_id) FROM account",
    "first_txn":    "SELECT MIN(transaction_date) FROM `transaction`",
    "last_txn":     "SELECT MAX(transaction_date) FROM `transaction`",
    "debits":       "SELECT COUNT(*) FROM `transaction` WHERE transaction_type='debit'",
    "credits":      "SELECT COUNT(*) FROM `transaction` WHERE transaction_type='credit'",
}

_NOT_A_FACT = r"(?!.*\b(most|money|spend|spent|pay|paid|largest|biggest|top|out of|balance|unusual|reconcil)\b)"
PATTERNS = [
    (rf"^{_NOT_A_FACT}.*(\b(how many|number of|total|count of|how much data)\b.*\b(transactions?|rows|records|txns?)\b|\btransactions? (count|total)\b|\bhow (big|large) is the (data|dataset)\b)", "transactions"),
    (rf"^{_NOT_A_FACT}.*\b(how many|number of|count of|total)\b.*\baccounts?\b", "accounts"),
    (rf"^{_NOT_A_FACT}.*\b(how many|number of|count of|which|list)\b.*\bbanks?\b", "banks"),
    (rf"^{_NOT_A_FACT}.*\b(how many|number of|count of|total)\b.*\b(debits?|payments? out|outgoing|withdrawals?)\b", "debits"),
    (rf"^{_NOT_A_FACT}.*\b(how many|number of|count of|total)\b.*\b(credits?|receipts?|incoming|deposits?)\b", "credits"),
    (rf"^{_NOT_A_FACT}.*(\b(date range|period|coverage|time ?span|from when|earliest|latest|oldest|newest|start date|end date|how far back)\b|\b(data|dataset) (cover|covers|span|spans|range|start|end)s?\b)", "range"),
    (rf"^{_NOT_A_FACT}.*\b(how many|number of|count of)\b.*\b(entities|customers|companies|clients|organisations|organizations)\b", "entities"),
]


def collect(verbose: bool = True) -> dict:
    src = _db.connect_source()
    facts = {}
    for k, sql in QUERIES.items():
        t = time.time()
        try:
            v = src.execute(sql).fetchone()[0]
            v = v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else (int(v) if v is not None else None)
            facts[k] = {"value": v, "sql": sql, "seconds": round(time.time() - t, 1)}
            if verbose: print(f"  {k:<14} {str(v):>14}   {facts[k]['seconds']:>6.1f}s")
        except Exception as e:                       # a slow or failing query costs one fact, not the cache
            if verbose: print(f"  {k:<14} FAILED {str(e)[:60]}")
    src.close()
    out = {"source": _db.source_label(), "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "facts": facts}
    PATH.write_text(json.dumps(out, indent=2))
    return out


def load() -> dict | None:
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return None


def _ev(n, label, fact, unit="count"):
    return {"id": f"ev_{n}", "label": label, "value": fact["value"], "unit": unit,
            "sql": fact["sql"], "params": [], "row_count": 1}


def answer(question: str, loaded: int | None = None) -> dict | None:
    """Return {answer, evidence, note} if the question is a cached source fact, else None."""
    cache = load()
    if not cache:
        return None
    q = (question or "").lower()
    kind = next((k for pat, k in PATTERNS if re.search(pat, q, re.I | re.S)), None)
    if not kind:
        return None
    f, src, at = cache["facts"], cache["source"], cache["collected_at"]
    note = f"Computed on the source database {src} at {at}; cached locally. Rerun: python -m finassist.facts"
    if kind == "range" and "first_txn" in f and "last_txn" in f:
        return {"answer": f"The source data covers {f['first_txn']['value']} to {f['last_txn']['value']}.",
                "evidence": [_ev(1, "first transaction", f["first_txn"], "text"), _ev(2, "last transaction", f["last_txn"], "text")],
                "note": note}
    if kind not in f:
        return None
    v = f[kind]["value"]
    noun = {"transactions": "transactions", "accounts": "accounts", "banks": "banks", "entities": "entities (account owners)",
            "debits": "debit transactions (money out)", "credits": "credit transactions (money in)"}[kind]
    text = f"There are {v:,} {noun} in the source database."
    if kind == "transactions" and loaded:
        text += f" {loaded:,} of them (August 2026) are loaded for detailed analysis."
    return {"answer": text, "evidence": [_ev(1, noun, f[kind])], "note": note}


if __name__ == "__main__":
    print(f"collecting facts from {_db.source_label()} ...")
    collect()
    print(f"  saved {PATH}")
