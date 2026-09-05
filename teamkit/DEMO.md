# Demo — 5 minutes

**Show a run, not the build.** Lead with the guardrail. Everyone else's demo stops at
"question in, paragraph out"; the thing worth watching is the answer that *refuses* to appear.

Open and close on:

> **"SQL computes. The model only reads the answer out loud — and we can prove it, per number,
> per query."**

## Bring-up (before judges arrive)

```bash
python -m finassist.enrich data/finance.sqlite   # ~10 s, idempotent
python -m api.server                             # http://localhost:8720
```

Header should read `100,000 transactions · 186 counterparties · data covers 2025-01-01 to
2026-09-05 · "today" = 2026-09-05`. If it does not, the enrichment step did not run.

## Beat sheet

| Time | Beat | On screen | Say |
|---|---|---|---|
| 0:00–0:30 | **The gap** | The schema doc, three tables | "The brief asks for vendor payouts, reconciliation status and categories. The schema has none of them. Vendor identity lives inside one free-text column, in nine narration formats. That gap *is* the problem." |
| 0:30–1:10 | **A real answer** | Preset 1 → *How much did we spend on vendor payouts last month?* | "₹69.5 crore, August 2026. Note the header — 'last month' is measured from the data's last transaction, not the wall clock. Against the sample data this question returns zero rows, and a careless assistant reports a spend of zero." |
| 1:10–1:40 | **Verifiability** | Expand **Evidence ledger** | "Four values, each with the SQL and its parameters. Fifty-row breakdown underneath. CSV export. You can rerun every number without me." |
| 1:40–2:10 | **Multi-turn** | Preset 2 → *How does that compare to the month before?* then ask *How much did we pay Blue Dart in August 2026?* and repeat the follow-up | "Same words, different subject — the second one stays scoped to Blue Dart, +76.6%. The session carries the vendor, not the arithmetic." |
| 2:10–2:50 | **The trap** | Preset 6 → *How much did we spend on Selection?* | "Nine counterparties contain the string SELECT and they are different businesses. A `LIKE '%SELECT%'` returns ₹139 crore. The right answer for Selection Mobile is ₹22 crore — **6.3x overstated**, stated with total confidence. So we don't guess. We list all five and ask." |
| 2:50–3:30 | **The money shot** | Red chip → **⛔ Try to make it fabricate a number** | "Here is a draft answer doctored with a confidence score and a pending figure. Same validator that gates every model reply. It names the tokens that appear in no query result and discards the draft. Fabrication isn't unlikely here — it's structurally impossible, and you just watched it fire." |
| 3:30–4:10 | **Refusals** | Presets 7, then type the forecast and Acme questions | "Out of range: it names the real window. Unknown vendor: it says so. Invoices and GST: no such data, and it says which data it *does* have. Five guards, all before a number is computed." |
| 4:10–4:40 | **The numbers** | Terminal: `python -m tests.eval_answers` then `python -m tests.eval_enrichment` | "20 out of 20 on a held-out answer key, with **zero model calls**. Rail parsing 100%. Vendor grouping 99.5% with zero wrong merges. The engine never reads the key." |
| 4:40–5:00 | **Close** | `ARCHITECTURE.md` diagram | "Standard library only, no pip install. The model is optional — it changes how the sentence reads, never what the number is. Repeat the pitch line." |

## If something breaks

- **Header says stats unavailable** → enrichment did not run. `python -m finassist.enrich data/finance.sqlite`.
- **Every answer is a template** → expected. No API key is configured; the numbers are unaffected. Say so plainly, it is a feature.
- **A question returns something odd** → read the intent chip under the answer. A wrong intent is visible; that is the design.

## Leave out

Auth, dark-mode toggle, settings, a chart library, anything that needs installing. The demo is
read at 1080p in five minutes and no further.
