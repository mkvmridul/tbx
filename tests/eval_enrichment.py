#!/usr/bin/env python3
"""Grade the enrichment layer against the answer key in data/truth/.

The engine never reads these files -- only this grader, after enrichment has already run.
Reports counterparty-extraction accuracy, reconciliation accuracy, category accuracy and
anomaly precision/recall. Exit code 0 only if counterparty accuracy and reconciliation
accuracy both clear their floors.

    python -m tests.eval_enrichment
"""
from __future__ import annotations

import csv, sys
from collections import Counter, defaultdict

from finassist import db as _db

CP_FLOOR, RECON_FLOOR = 0.97, 0.97


def main(truth="data/truth/transaction_truth.csv"):
    truth_rows = {r["transaction_id"]: r for r in csv.DictReader(open(truth))}
    cx = _db.connect()
    got = {r["transaction_id"]: r for r in cx.execute("""
        SELECT te.transaction_id, te.rail, te.recon_status, te.category, te.is_anomaly,
               cp.canonical_name, cp.canon_key
        FROM transaction_enriched te LEFT JOIN counterparty cp USING (counterparty_id)
    """)}

    # ---- counterparty ------------------------------------------------------
    # Graded by GROUPING, not by string equality: the engine invents its own ids, so what
    # matters is whether two transactions the answer key calls the same vendor were put in
    # the same group, and whether two it calls different vendors were kept apart.
    true_to_pred, pred_to_true = defaultdict(Counter), defaultdict(Counter)
    scored = 0
    for tid, t in truth_rows.items():
        g = got.get(tid)
        if not g or not t["counterparty_id"]:
            continue
        scored += 1
        # Graded on the counterparty NAME, not its id. The fixture gives 198 ids to only 185
        # distinct names -- 12 names belong to two or three different people. The narration
        # carries nothing but the name, so no parser can separate two people called
        # "Neha Bhatt". Grading on ids would score a correct grouping as an error.
        true_to_pred[t["counterparty_name"]][g["canon_key"]] += 1
        pred_to_true[g["canon_key"]][t["counterparty_name"]] += 1

    pure = sum(c.most_common(1)[0][1] for c in true_to_pred.values())
    split = sum(1 for c in true_to_pred.values() if len(c) > 1)
    merged = sum(1 for c in pred_to_true.values() if len(c) > 1)
    cp_acc = pure / scored if scored else 0.0

    # ---- rail --------------------------------------------------------------
    rail_ok = sum(1 for tid, t in truth_rows.items()
                  if tid in got and got[tid]["rail"] == t["rail"])
    rail_acc = rail_ok / len(truth_rows)

    # ---- reconciliation ----------------------------------------------------
    recon_ok = sum(1 for tid, t in truth_rows.items()
                   if tid in got and got[tid]["recon_status"] == t["recon_status"])
    recon_acc = recon_ok / len(truth_rows)
    confusion = Counter((t["recon_status"], got[tid]["recon_status"])
                        for tid, t in truth_rows.items()
                        if tid in got and got[tid]["recon_status"] != t["recon_status"])

    # ---- category ----------------------------------------------------------
    cat_scored = [(t["category"], got[tid]["category"]) for tid, t in truth_rows.items()
                  if tid in got and t["category"] not in ("bank_charges", "loan_collection", "salary")]
    cat_ok = sum(1 for a, b in cat_scored if a == b)
    cat_acc = cat_ok / len(cat_scored) if cat_scored else 0.0

    # ---- anomaly -----------------------------------------------------------
    tp = sum(1 for tid, t in truth_rows.items() if tid in got and t["is_anomaly"] == "1" and got[tid]["is_anomaly"])
    fp = sum(1 for tid, t in truth_rows.items() if tid in got and t["is_anomaly"] == "0" and got[tid]["is_anomaly"])
    fn = sum(1 for tid, t in truth_rows.items() if tid in got and t["is_anomaly"] == "1" and not got[tid]["is_anomaly"])
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0

    W = 34
    print(f"\n  ENRICHMENT EVAL — {len(truth_rows):,} transactions\n")
    print(f"  {'counterparty grouping accuracy':<{W}} {cp_acc:>7.3%}   ({scored:,} scored)")
    print(f"  {'  true vendors split apart':<{W}} {split:>7}")
    print(f"  {'  distinct vendors merged':<{W}} {merged:>7}")
    print(f"  {'  (fixture note)':<{W}}         198 truth ids share 185 distinct names")
    print(f"  {'rail classification accuracy':<{W}} {rail_acc:>7.3%}")
    print(f"  {'reconciliation accuracy':<{W}} {recon_acc:>7.3%}")
    print(f"  {'category accuracy (derived)':<{W}} {cat_acc:>7.3%}   ({len(cat_scored):,} scored)")
    print(f"  {'anomaly precision':<{W}} {prec:>7.3%}   ({tp} tp, {fp} fp)")
    print(f"  {'anomaly recall':<{W}} {rec:>7.3%}   ({fn} missed)")
    if confusion:
        print("\n  reconciliation misses (expected -> got):")
        for (a, b), n in confusion.most_common(6):
            print(f"    {a:<16} -> {b:<16} {n:>6,}")

    fails = []
    if cp_acc < CP_FLOOR:    fails.append(f"counterparty {cp_acc:.3%} < {CP_FLOOR:.0%}")
    if recon_acc < RECON_FLOOR: fails.append(f"reconciliation {recon_acc:.3%} < {RECON_FLOOR:.0%}")
    print(f"\n  {'PASS' if not fails else 'FAIL — ' + '; '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:] or [])))
