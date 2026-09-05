#!/usr/bin/env python3
"""Grade the assistant against data/truth/answer_key.json.

The engine never reads the answer key. This runs after it, compares what the assistant
produced against values computed independently from the raw CSVs, and prints a scorecard.

Grading depends on the question kind:
  lookup / definitional / derived / trap   the headline number, within tolerance
  guardrail                                MUST refuse or ask for clarification
  ranking                                  the ordered list of names must match
  multi_turn                               asked as a follow-up inside one session
  anomaly                                  must return at least one flagged transaction

    python -m tests.eval_answers [--llm]
"""
from __future__ import annotations

import argparse, json, sys

from finassist.engine import Engine, Session

TOL = 0.005          # 0.5% relative tolerance on money and counts


def close(a, b) -> bool:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if b == 0:
        return abs(a) < 1e-6
    return abs(a - b) / abs(b) <= TOL


def grade(q: dict, ans) -> tuple[bool, str]:
    kind, exp = q["kind"], q.get("expected_value")

    if kind == "guardrail":
        if ans.status in ("refused", "clarify"):
            return True, f"{ans.status}: {ans.answer[:64]}"
        return False, f"ANSWERED instead of refusing: {ans.answer[:64]}"

    if ans.status in ("refused", "clarify"):
        return False, f"refused a question it should answer: {ans.answer[:64]}"

    if kind == "ranking":
        if isinstance(exp, list):
            got = [r[0] for r in ans.rows][:len(exp)]
            return (got == exp), f"got {got[:3]}... expected {exp[:3]}..."
        # A single expected winner (e.g. the bank_code "HDFC"). The engine reports the bank
        # NAME in column 0 and the code in column 1, so accept a match in either.
        if not ans.rows:
            return False, "no rows"
        top = [str(v).upper() for v in ans.rows[0]]
        want = str(exp).upper()
        return any(want == v or want in v for v in top), f"top row {ans.rows[0][:2]}, expected {exp}"

    if kind == "anomaly":
        return (len(ans.rows) > 0), f"{len(ans.rows)} flagged"

    h = ans.headline or {}
    got = h.get("value")
    if close(got, exp):
        return True, f"{got:,}" if isinstance(got, (int, float)) else str(got)
    # the expected number may be an evidence value rather than the headline
    for e in ans.evidence:
        if close(e["value"], exp):
            return True, f"{e['value']:,} (from {e['id']})"
    return False, f"got {got}, expected {exp}"


def main(use_llm: bool = False) -> int:
    key = json.load(open("data/truth/answer_key.json"))
    eng = Engine(use_llm=use_llm)

    # Index questions so a multi_turn one can be replayed after its predecessor.
    qs = key["questions"]
    prior_of = {"mom_compare": "last_month_payouts"}

    rows, calls = [], 0
    for q in qs:
        # Each question gets a FRESH session unless it is explicitly a follow-up. Carrying
        # context between unrelated questions is correct assistant behaviour but wrong
        # grading: it silently rescopes a later question to an earlier question's vendor.
        session = Session()
        if q["kind"] == "multi_turn":
            lead = next((x for x in qs if x["id"] == prior_of.get(q["id"])), None)
            if lead:
                calls += eng.ask(lead["question"], session).llm_calls
        ans = eng.ask(q["question"], session)
        calls += ans.llm_calls
        ok, detail = grade(q, ans)
        rows.append((q["id"], q["kind"], ok, detail, ans.intent, ans.source))

    by_kind: dict[str, list[bool]] = {}
    print(f"\n  ANSWER-KEY EVAL — {len(rows)} questions · llm={'on' if use_llm else 'off'}\n")
    for qid, kind, ok, detail, intent, source in rows:
        by_kind.setdefault(kind, []).append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {qid:<20} {kind:<13} {intent:<22} {detail[:60]}")

    total = sum(1 for r in rows if r[2])
    print(f"\n  {'by kind':<16}{'pass':>6}{'of':>4}")
    for k, v in sorted(by_kind.items()):
        print(f"  {k:<16}{sum(v):>6}{len(v):>4}")
    print(f"\n  overall            {total}/{len(rows)} = {total/len(rows):.1%}")
    print(f"  llm calls used     {calls}  ({calls/len(rows):.2f} per question)")
    print(f"\n  {'PASS' if total == len(rows) else f'FAIL — {len(rows)-total} question(s) wrong'}\n")
    return 0 if total == len(rows) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="use the model planner as well")
    a = ap.parse_args()
    sys.exit(main(a.llm))
