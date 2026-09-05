# Sample questions and answers

Transcribed from an actual run — `python -m tests.make_samples` regenerates this file.

Dataset: **100,000 transactions**, 186 counterparties, 80 accounts, covering **2025-01-01 to 2026-09-05**. "Today" is the last transaction date, 2026-09-05, not the wall clock.

Every answer below was produced with **zero language-model calls**. The model is optional; it rewrites the prose when configured, and the numbers are identical either way because they come from SQL.

## A grounded lookup

**Q — How much did we spend on vendor payouts last month?**

> Total paid out in August 2026: Rs 695,439,580.02. Number of payments 3,814; total received Rs 293,905,080.24.

<sub>`spend_total` · answered · 48 ms · 0 llm calls · period `August 2026`</sub>

Headline: **Rs 695,439,580.02** — total paid out (`ev_1`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | Rs 695,439,580.02 | total paid out |
| `ev_2` | 3,814 | number of payments |
| `ev_3` | Rs 293,905,080.24 | total received |
| `ev_4` | 50 | breakdown by counterparty |

The first query behind these values:

```sql
SELECT COALESCE(SUM(amount),0) FROM transaction_enriched WHERE 1=1 AND txn_date BETWEEN ? AND ? AND rail <> 'CHARGES' AND transaction_type='debit'
```

</details>

Breakdown (50 rows, first 5; CSV export available):

| counterparty | transactions | total_paid |
|---|---|---|
| RMZ CORP PRIVATE LIMITED | 59 | 51,704,346.22 |
| GST PAYMENT CBDT | 56 | 44,517,668.03 |
| SHARDUL AMARCHAND MANGALDAS | 59 | 36,655,691.38 |
| QUESS CORP LIMITED | 57 | 35,923,092.80 |
| BRIGADE ENTERPRISES LIMITED | 61 | 34,879,281.06 |

---

**Q — How does that compare to the month before?**

> Change vs July 2026 in August 2026: -16.0%. Paid in August 2026 Rs 695,439,580.02; paid in July 2026 Rs 827,597,556.91.

<sub>`compare_periods` · answered · 14 ms · 0 llm calls · period `August 2026`</sub>

Headline: **-16.0%** — change vs July 2026 (`ev_3`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | Rs 695,439,580.02 | paid in August 2026 |
| `ev_2` | Rs 827,597,556.91 | paid in July 2026 |
| `ev_3` | Rs -132,157,976.89 | change A minus B |
| `ev_4` | -16.0% | change as a percentage of B |

The first query behind these values:

```sql
SELECT COALESCE(SUM(amount),0) FROM transaction_enriched WHERE 1=1 AND txn_date BETWEEN ? AND ? AND rail <> 'CHARGES' AND transaction_type='debit'
```

</details>

Breakdown (2 rows, first 2; CSV export available):

| period | total_paid |
|---|---|
| August 2026 | 695,439,580.02 |
| July 2026 | 827,597,556.91 |

---

## Following one vendor across turns

**Q — How much did we pay Blue Dart in August 2026?**

> Paid to BLUE DART EXPRESS LIMITED in August 2026: Rs 3,425,730.36. Number of payments to BLUE DART EXPRESS LIMITED 59; credits received from BLUE DART EXPRESS LIMITED Rs 1,778,896.66.

<sub>`spend_by_counterparty` · answered · 5 ms · 0 llm calls · period `August 2026` · scope `BLUE DART EXPRESS LIMITED`</sub>

Headline: **Rs 3,425,730.36** — paid to BLUE DART EXPRESS LIMITED (`ev_1`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | Rs 3,425,730.36 | total paid to BLUE DART EXPRESS LIMITED |
| `ev_2` | 59 | number of payments to BLUE DART EXPRESS LIMITED |
| `ev_3` | Rs 1,778,896.66 | credits received from BLUE DART EXPRESS LIMITED |
| `ev_4` | 93 | underlying transactions with BLUE DART EXPRESS LIMITED |

The first query behind these values:

```sql
SELECT COALESCE(SUM(amount),0) FROM transaction_enriched WHERE 1=1 AND txn_date BETWEEN ? AND ? AND counterparty_id = ? AND transaction_type='debit'
```

</details>

Breakdown (93 rows, first 5; CSV export available):

| date | type | amount | rail | reconciliation | reference |
|---|---|---|---|---|---|
| 2026-08-24 | debit | 334,211.97 | NEFT_SPACED | unmatched | 36711179 |
| 2026-08-06 | debit | 246,713.92 | UPI | unmatched | 427422203364 |
| 2026-08-03 | debit | 236,772.50 | FT_SPACED | matched_pair | 70419517 |
| 2026-08-03 | credit | 236,772.50 | FT_SPACED | matched_pair | 87628430 |
| 2026-08-13 | debit | 235,106.68 | NEFT_SLASH | unmatched | 658876641975 |

> **Note.** BLUE DART EXPRESS LIMITED also sent 1,778,896.66 in credits (refunds or receipts); the headline figure is payments out only.

---

**Q — How does that compare to the month before?**

> Change vs July 2026 in August 2026: +76.6%. Paid in August 2026 Rs 3,425,730.36; paid in July 2026 Rs 1,939,907.95.

<sub>`compare_periods` · answered · 1 ms · 0 llm calls · period `August 2026` · scope `BLUE DART EXPRESS LIMITED`</sub>

Headline: **+76.6%** — change vs July 2026 (`ev_3`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | Rs 3,425,730.36 | paid in August 2026 |
| `ev_2` | Rs 1,939,907.95 | paid in July 2026 |
| `ev_3` | Rs 1,485,822.41 | change A minus B |
| `ev_4` | +76.6% | change as a percentage of B |

The first query behind these values:

```sql
SELECT COALESCE(SUM(amount),0) FROM transaction_enriched WHERE 1=1 AND txn_date BETWEEN ? AND ? AND counterparty_id = ? AND rail <> 'CHARGES' AND transaction_type='debit'
```

</details>

Breakdown (2 rows, first 2; CSV export available):

| period | total_paid |
|---|---|
| August 2026 | 3,425,730.36 |
| July 2026 | 1,939,907.95 |

---

**Q — Show me that by month**

> Highest month: 2026-05: Rs 3,502,344.83. Monthly totals 21; lowest month: 2026-09 Rs 581,723.60.

<sub>`spend_trend` · answered · 1 ms · 0 llm calls · scope `BLUE DART EXPRESS LIMITED`</sub>

Headline: **Rs 3,502,344.83** — highest month: 2026-05 (`ev_2`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | 21 | monthly totals |
| `ev_2` | Rs 3,502,344.83 | highest month: 2026-05 |
| `ev_3` | Rs 581,723.60 | lowest month: 2026-09 |

The first query behind these values:

```sql
SELECT txn_month, COUNT(*) AS txns, ROUND(SUM(amount),2) AS total
              FROM transaction_enriched WHERE 1=1 AND counterparty_id = ? AND rail <> 'CHARGES' AND transaction_type='debit'
              GROUP BY 1 ORDER BY 1
```

</details>

Breakdown (21 rows, first 5; CSV export available):

| month | transactions | total_paid |
|---|---|---|
| 2025-01 | 31 | 1,946,755.66 |
| 2025-02 | 33 | 2,155,405.07 |
| 2025-03 | 32 | 1,326,639.16 |
| 2025-04 | 31 | 2,406,033.16 |
| 2025-05 | 32 | 983,083.94 |

---

## Reconciliation

**Q — Which transactions are still unreconciled?**

> Unreconciled transactions: 50,107. Total value unreconciled Rs 9,063,626,513.29; largest unreconciled transactions 200.

<sub>`unreconciled` · answered · 27 ms · 0 llm calls</sub>

Headline: **50,107** — unreconciled transactions (`ev_1`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | 50,107 | unreconciled transactions |
| `ev_2` | Rs 9,063,626,513.29 | total value unreconciled |
| `ev_3` | 200 | largest unreconciled transactions |

The first query behind these values:

```sql
SELECT COUNT(*) FROM transaction_enriched WHERE 1=1 AND recon_status = 'unmatched'
```

</details>

Breakdown (200 rows, first 5; CSV export available):

| date | counterparty | type | amount | rail | reference |
|---|---|---|---|---|---|
| 2026-07-10 | DENTSU AEGIS NETWORK INDIA | debit | 43,731,402.09 | NEFT_SPACED | 93916255 |
| 2025-09-18 | RMZ CORP PRIVATE LIMITED | debit | 25,557,837.33 | NEFT_SLASH | 625559173147 |
| 2026-07-22 | GST PAYMENT CBDT | debit | 20,493,280.90 | NEFT_SLASH | 180930093000 |
| 2025-08-04 | INCOME TAX DEPARTMENT TDS | debit | 19,858,078.79 | FT_COMPACT | KNFQUB9633809092 |
| 2026-07-29 | META PLATFORMS IRELAND LTD | debit | 17,243,919.22 | NEFT_SLASH | 521515374007 |

> **Note.** Reconciliation status is not a column in the source schema. A transaction counts as reconciled here when it has a matching counterpart sharing a UTR and reference, or carries a structured ZBFL...PBL loan reference. Everything else is unmatched.

---

**Q — How many were there in August 2026?**

> Unreconciled transactions in August 2026: 3,225. Total value unreconciled Rs 596,798,984.97; largest unreconciled transactions 200.

<sub>`unreconciled` · answered · 26 ms · 0 llm calls · period `August 2026`</sub>

Headline: **3,225** — unreconciled transactions (`ev_1`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | 3,225 | unreconciled transactions |
| `ev_2` | Rs 596,798,984.97 | total value unreconciled |
| `ev_3` | 200 | largest unreconciled transactions |

The first query behind these values:

```sql
SELECT COUNT(*) FROM transaction_enriched WHERE 1=1 AND txn_date BETWEEN ? AND ? AND recon_status = 'unmatched'
```

</details>

Breakdown (200 rows, first 5; CSV export available):

| date | counterparty | type | amount | rail | reference |
|---|---|---|---|---|---|
| 2026-08-21 | GST PAYMENT CBDT | debit | 15,163,931.33 | NEFT_SPACED | 52607526 |
| 2026-08-29 | SELECTION MALIGAI | credit | 7,022,146.42 | NEFT_SPACED | 48411866 |
| 2026-08-12 | SELECT MOBILES | debit | 5,762,584.05 | RTGS | 53989896 |
| 2026-08-01 | QUESS CORP LIMITED | debit | 5,381,526.29 | UPI | 233476553012 |
| 2026-08-17 | EMBASSY OFFICE PARKS REIT | debit | 5,145,545.93 | RTGS | 51190101 |

> **Note.** Reconciliation status is not a column in the source schema. A transaction counts as reconciled here when it has a matching counterpart sharing a UTR and reference, or carries a structured ZBFL...PBL loan reference. Everything else is unmatched.

---

## Ranking and categories

**Q — Who are our top 5 vendors by total spend?**

> Largest counterparty: RMZ CORP PRIVATE LIMITED: Rs 689,933,886.11. Top 5 counterparties by spend 5.

<sub>`top_counterparties` · answered · 36 ms · 0 llm calls</sub>

Headline: **Rs 689,933,886.11** — largest counterparty: RMZ CORP PRIVATE LIMITED (`ev_2`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | 5 | top 5 counterparties by spend |
| `ev_2` | Rs 689,933,886.11 | largest: RMZ CORP PRIVATE LIMITED |

The first query behind these values:

```sql
SELECT cp.canonical_name, COUNT(*) AS txns, ROUND(SUM(te.amount),2) AS total
              FROM transaction_enriched te JOIN counterparty cp USING (counterparty_id)
              WHERE 1=1 AND counterparty_id IS NOT NULL AND te.transaction_type='debit'
              GROUP BY 1 ORDER BY total DESC LIMIT 5
```

</details>

Breakdown (5 rows, first 5; CSV export available):

| counterparty | transactions | total_paid |
|---|---|---|
| RMZ CORP PRIVATE LIMITED | 771 | 689,933,886.11 |
| GST PAYMENT CBDT | 830 | 591,385,719.58 |
| EMBASSY OFFICE PARKS REIT | 791 | 540,141,543.51 |
| QUESS CORP LIMITED | 836 | 514,080,616.70 |
| META PLATFORMS IRELAND LTD | 861 | 493,013,864.63 |

---

**Q — What was our total rent spend?**

> Total paid out: Rs 1,999,718,142.24. Number of payments 3,249; total received Rs 853,108,141.43.

<sub>`spend_total` · answered · 16 ms · 0 llm calls</sub>

Headline: **Rs 1,999,718,142.24** — total paid out (`ev_1`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | Rs 1,999,718,142.24 | total paid out |
| `ev_2` | 3,249 | number of payments |
| `ev_3` | Rs 853,108,141.43 | total received |
| `ev_4` | 4 | breakdown by counterparty |

The first query behind these values:

```sql
SELECT COALESCE(SUM(amount),0) FROM transaction_enriched WHERE 1=1 AND category = ? AND rail <> 'CHARGES' AND transaction_type='debit'
```

</details>

Breakdown (4 rows, first 4; CSV export available):

| counterparty | transactions | total_paid |
|---|---|---|
| RMZ CORP PRIVATE LIMITED | 771 | 689,933,886.11 |
| EMBASSY OFFICE PARKS REIT | 791 | 540,141,543.51 |
| BRIGADE ENTERPRISES LIMITED | 840 | 461,005,237.63 |
| DLF CYBER CITY DEVELOPERS | 847 | 308,637,474.99 |

> **Note.** Category is not a column in the source schema. It is derived from the counterparty name, so treat it as an approximation.

---

**Q — Which bank do we move the most money out of?**

> Largest bank by outflow: HDFC BANK LIMITED: Rs 2,645,724,968.39. Banks with outgoing payments 10.

<sub>`spend_by_bank` · answered · 45 ms · 0 llm calls</sub>

Headline: **Rs 2,645,724,968.39** — largest bank by outflow: HDFC BANK LIMITED (`ev_2`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | 10 | banks with outgoing payments |
| `ev_2` | Rs 2,645,724,968.39 | largest: HDFC BANK LIMITED |

The first query behind these values:

```sql
SELECT b.bank_name, b.bank_code, COUNT(*) AS txns, ROUND(SUM(te.amount),2) AS total
              FROM transaction_enriched te
              JOIN account a ON a.account_id = te.account_id
              JOIN bank b ON b.bank_code = a.bank_code
              WHERE 1=1 AND te.transaction_type='debit'
              GROUP BY 1,2 ORDER BY total DESC
```

</details>

Breakdown (10 rows, first 5; CSV export available):

| bank | bank_code | transactions | total_paid |
|---|---|---|---|
| HDFC BANK LIMITED | HDFC | 15,856 | 2,645,724,968.39 |
| STATE BANK OF INDIA | SBIN | 12,286 | 1,956,833,809.31 |
| KOTAK MAHINDRA BANK LIMITED | KKBK | 9,105 | 1,545,490,192.02 |
| AXIS BANK LIMITED | UTIB | 8,266 | 1,328,618,385.20 |
| AU SMALL FINANCE BANK LIMITED | AUBL | 5,695 | 965,227,432.23 |

---

## Anomalies

**Q — Was there anything unusual about our largest payments?**

> Unusual payments: 17. Largest outlier: RMZ CORP PRIVATE LIMITED Rs 25,557,837.33.

<sub>`anomalies` · answered · 0 ms · 0 llm calls</sub>

Headline: **17** — unusual payments (`ev_1`)

<details><summary>Evidence ledger</summary>

| id | value | what it is |
|---|---|---|
| `ev_1` | 17 | unusual payments found |
| `ev_2` | Rs 25,557,837.33 | largest outlier: RMZ CORP PRIVATE LIMITED |

The first query behind these values:

```sql
SELECT COUNT(*) FROM transaction_enriched WHERE 1=1 AND is_anomaly = 1
```

</details>

Breakdown (17 rows, first 5; CSV export available):

| date | counterparty | amount | robust_z | rail |
|---|---|---|---|---|
| 2025-09-18 | RMZ CORP PRIVATE LIMITED | 25,557,837.33 | 5.93 | NEFT_SLASH |
| 2026-03-26 | DLF CYBER CITY DEVELOPERS | 6,717,832.64 | 5.20 | NEFT_SPACED |
| 2026-01-30 | ADANI ELECTRICITY MUMBAI LTD | 6,311,763.01 | 5.20 | NEFT_SLASH |
| 2026-07-10 | DENTSU AEGIS NETWORK INDIA | 43,731,402.09 | 4.92 | NEFT_SPACED |
| 2026-04-11 | ASHOK AGARWAL | 488,757.96 | 4.84 | UPI |

> **Note.** Unusual means the payment is more than 4 robust standard deviations above that counterparty's own median, measured in log space. It is a statistical flag, not a finding of error.

---

## Guardrails — the assistant must refuse or ask, never invent

**Q — How much did we spend on Selection?**

> “Selection” matches 5 different counterparties in the data: SELECTION MOBILE SHOP, NAVYUG SELECTIONS, SELECTION ELECTRONICS PVT LTD, SELECTION MALIGAI, UMANG SELECTIONHAPURBPES. Which one did you mean?

<sub>`spend_by_counterparty` · ASKED FOR CLARIFICATION (guardrail) · 0 ms · 0 llm calls</sub>

Candidates offered: `SELECTION MOBILE SHOP`, `NAVYUG SELECTIONS`, `SELECTION ELECTRONICS PVT LTD`, `SELECTION MALIGAI`, `UMANG SELECTIONHAPURBPES`

---

**Q — How much did we pay Acme Corporation last quarter?**

> No counterparty matching “Acme Corporation” appears in the data.

<sub>`spend_by_counterparty` · REFUSED (guardrail) · 0 ms · 0 llm calls</sub>

---

**Q — How much did we spend on logistics in November 2024?**

> No data for November 2024. This dataset covers 2025-01-01 to 2026-09-05, so there are no transactions in that period.

<sub>`spend_total` · REFUSED (guardrail) · 0 ms · 0 llm calls · period `November 2024`</sub>

---

**Q — What is the GST component of our March 2026 invoices?**

> The data has no tax breakdown. It holds bank transactions only, with no invoice or line-item detail.

<sub>`unsupported` · REFUSED (guardrail) · 0 ms · 0 llm calls</sub>

---

**Q — What will we spend next month?**

> The data ends at the last recorded transaction and contains no forecast.

<sub>`unsupported` · REFUSED (guardrail) · 0 ms · 0 llm calls</sub>

---

**Q — Who is our largest customer?**

> The data does not label counterparties as customers or suppliers, so it cannot rank customers. It can rank counterparties by money received.

<sub>`unsupported` · REFUSED (guardrail) · 0 ms · 0 llm calls</sub>

---

## Totals

- Questions run: **17**
- Language-model calls: **0**
- Answer-key score: **20/20** (`python -m tests.eval_answers`)
