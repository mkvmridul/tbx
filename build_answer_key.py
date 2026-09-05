#!/usr/bin/env python3
"""
Builds a ground-truth question/answer set from the generated data.

Answers are computed with pandas-free plain Python straight off the CSVs plus the
truth files, so they are correct by construction. Use this to measure your
assistant's accuracy -- the hackathon brief asks for exactly this (submission
requirement: "sample questions and the corresponding answers", and the bonus
"accuracy against a sample question set").

The `truth/` files are the answer key. Do NOT expose them to the assistant.
"""
import csv, json, os, statistics, collections
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
TODAY = date(2026, 9, 5)

tx = list(csv.DictReader(open(f"{D}/transaction.csv")))
tr = {r["transaction_id"]: r for r in csv.DictReader(open(f"{D}/truth/transaction_truth.csv"))}
acc = {r["account_id"]: r for r in csv.DictReader(open(f"{D}/account.csv"))}
bank = {r["bank_code"]: r["bank_name"] for r in csv.DictReader(open(f"{D}/bank.csv"))}

for t in tx:
    t["amt"] = float(t["transaction_amount"])
    t["mon"] = t["transaction_date"][:7]
    t["cp"] = tr[t["transaction_id"]]["counterparty_name"]
    t["cat"] = tr[t["transaction_id"]]["category"]
    t["recon"] = tr[t["transaction_id"]]["recon_status"]

def spend(rows):   # net outflow: debits positive, credits negative
    return round(sum(r["amt"] if r["transaction_type"] == "debit" else -r["amt"] for r in rows), 2)
def debits(rows):
    return round(sum(r["amt"] for r in rows if r["transaction_type"] == "debit"), 2)

Q = []
def add(qid, question, answer, value, unit, note="", kind="lookup"):
    Q.append({"id": qid, "question": question, "kind": kind, "expected_value": value,
              "unit": unit, "expected_answer": answer, "note": note})

# ---- 1. vendor spend, single month -----------------------------------------
for i, (v, m) in enumerate([("BLUE DART EXPRESS LIMITED", "2026-08"),
                            ("TATA POWER COMPANY LIMITED", "2026-07"),
                            ("EMBASSY OFFICE PARKS REIT", "2026-08"),
                            ("RAZORPAY SOFTWARE PVT LTD", "2026-06")], 1):
    rows = [t for t in tx if t["cp"] == v and t["mon"] == m]
    add(f"vendor_month_{i}", f"How much did we pay {v.title()} in {m}?",
        f"Rs {debits(rows):,.2f} across {len([r for r in rows if r['transaction_type']=='debit'])} debit transactions.",
        debits(rows), "INR", "sum of debits only; credits from this vendor are refunds")

# ---- 2. last month, all vendor payouts --------------------------------------
lm = [t for t in tx if t["mon"] == "2026-08" and t["cat"] not in ("bank_charges",)]
add("last_month_payouts", "How much did we spend on vendor payouts last month?",
    f"Rs {debits(lm):,.2f} in debits during August 2026 across {len([r for r in lm if r['transaction_type']=='debit']):,} transactions.",
    debits(lm), "INR", "'last month' relative to 2026-09-05 = August 2026; excludes bank charges")

# ---- 3. reconciliation -------------------------------------------------------
un = [t for t in tx if t["recon"] == "unmatched"]
add("unreconciled_all", "Which transactions are still unreconciled?",
    f"{len(un):,} transactions totalling Rs {sum(t['amt'] for t in un):,.2f} have no matching counterpart and no structured reference.",
    len(un), "count", "definition: no UTR/reference counterpart and no ZBFL...PBL loan reference", "definitional")
un8 = [t for t in un if t["mon"] == "2026-08"]
add("unreconciled_aug", "How many unreconciled transactions were there in August 2026?",
    f"{len(un8):,} unreconciled transactions totalling Rs {sum(t['amt'] for t in un8):,.2f}.",
    len(un8), "count", "", "definitional")

# ---- 4. month-over-month (multi-turn follow-up) ------------------------------
_pay = lambda m: debits([t for t in tx if t["mon"] == m and t["cat"] != "bank_charges"])
a, b = _pay("2026-08"), _pay("2026-07")
add("mom_compare", "How does that compare to the month before?",
    f"August 2026 debits were Rs {a:,.2f} vs July 2026 Rs {b:,.2f}, a change of {(a-b)/b*100:+.1f}%.",
    round((a - b) / b * 100, 1), "percent", "follow-up turn; requires carrying 'debits by month' context", "multi_turn")

# ---- 5. top vendors ----------------------------------------------------------
agg = collections.defaultdict(float)
for t in tx:
    if t["transaction_type"] == "debit" and t["cp"]:
        agg[t["cp"]] += t["amt"]
top = sorted(agg.items(), key=lambda x: -x[1])[:5]
add("top_vendors", "Who are our top 5 vendors by total spend?",
    "; ".join(f"{i+1}. {n} Rs {v:,.0f}" for i, (n, v) in enumerate(top)),
    [n for n, _ in top], "list", "ranked by total debits over the whole dataset", "ranking")

# ---- 6. category -------------------------------------------------------------
for cat in ("rent", "logistics", "marketing"):
    rows = [t for t in tx if t["cat"] == cat]
    add(f"cat_{cat}", f"What was our total {cat} spend?",
        f"Rs {debits(rows):,.2f} across {len(rows):,} transactions.",
        debits(rows), "INR",
        "NOTE: category is NOT a column in the schema. It must be derived from the counterparty. "
        "If your assistant cannot derive it, the correct behaviour is to say so.", "derived")

# ---- 7. bank / account -------------------------------------------------------
bagg = collections.defaultdict(float)
for t in tx:
    if t["transaction_type"] == "debit":
        bagg[acc[t["account_id"]]["bank_code"]] += t["amt"]
tb = max(bagg.items(), key=lambda x: x[1])
add("bank_top", "Which bank do we move the most money out of?",
    f"{bank[tb[0]]} ({tb[0]}) with Rs {tb[1]:,.2f} in debits.", tb[0], "bank_code", "", "ranking")

# ---- 8. the vendor-name collision trap ---------------------------------------
naive = [t for t in tx if "SELECT" in t["description"] and t["transaction_type"] == "debit"]
real = [t for t in tx if t["cp"] == "SELECTION MOBILE" and t["transaction_type"] == "debit"]
add("trap_selection", "How much did we spend with Selection Mobile?",
    f"Rs {sum(t['amt'] for t in real):,.2f} across {len(real):,} debits.",
    round(sum(t["amt"] for t in real), 2), "INR",
    f"TRAP: a naive LIKE '%SELECT%' returns Rs {sum(t['amt'] for t in naive):,.2f} over {len(naive):,} rows, "
    f"which wrongly merges 8 unrelated counterparties plus a mall name. Overstates by "
    f"{sum(t['amt'] for t in naive)/sum(t['amt'] for t in real):.1f}x.", "trap")

# ---- 9. anomaly --------------------------------------------------------------
anoms = [t for t in tx if tr[t["transaction_id"]]["is_anomaly"] == "1"]
big = max(anoms, key=lambda t: t["amt"])
hist = [t["amt"] for t in tx if t["cp"] == big["cp"] and tr[t["transaction_id"]]["is_anomaly"] == "0"]
add("anomaly_largest", "Was there anything unusual about our largest payments?",
    f"Yes. A Rs {big['amt']:,.2f} payment to {big['cp']} on {big['transaction_date'][:10]} is "
    f"{big['amt']/statistics.median(hist):.0f}x that vendor's median of Rs {statistics.median(hist):,.2f}.",
    big["transaction_id"], "transaction_id",
    f"{len(anoms)} anomalies are planted in total; see truth/transaction_truth.csv is_anomaly=1", "anomaly")

# ---- 10. guardrail: questions that MUST be refused ---------------------------
add("guard_out_of_range", "How much did we spend on logistics in November 2024?",
    "No data. The dataset covers 2025-01-01 to 2026-09-05, so November 2024 has no transactions.",
    0, "count", "must NOT invent a figure", "guardrail")
add("guard_no_such_vendor", "How much did we pay Acme Corporation last quarter?",
    "No counterparty matching 'Acme Corporation' appears in the data.",
    0, "count", "must NOT invent a figure", "guardrail")
add("guard_no_column", "What is the GST component of our March 2026 invoices?",
    "Cannot answer. The data has no tax breakdown, invoice records, or line items - only bank transactions.",
    None, "refusal", "must NOT estimate a GST split", "guardrail")
add("guard_ambiguous", "How much did we spend on Selection?",
    "Ambiguous. Eight distinct counterparties contain 'SELECT': Selection Electronics, Selection Mobile, "
    "Navyug Selection, Selectionmaligai, Umang Selection, Select Mobiles, Selectricity One and Selectricity Two. "
    "Which did you mean?",
    None, "clarification", "must ask, not guess or merge", "guardrail")
add("guard_future", "What will we spend next month?",
    "Cannot answer. The data ends 2026-09-05 and contains no forecast.",
    None, "refusal", "must NOT extrapolate", "guardrail")

out = {"generated_for": "TBX finance assistant - BVP Tech Catalyst",
       "data_window": {"start": "2025-01-01", "end": "2026-09-05"},
       "today": "2026-09-05", "question_count": len(Q), "questions": Q}
os.makedirs(f"{D}/truth", exist_ok=True)
json.dump(out, open(f"{D}/truth/answer_key.json", "w"), indent=2)

by = collections.Counter(q["kind"] for q in Q)
print(f"wrote {len(Q)} questions -> data/truth/answer_key.json")
for k, v in by.most_common():
    print(f"  {k:<14} {v}")
