# Architecture

**finassist** — a conversational finance assistant for the TBX schema. A question arrives in
plain English; the engine resolves it to a fixed intent, computes the answer in SQL, and the
language model writes prose around numbers it is structurally incapable of inventing.

The one sentence that describes the whole design:

> **SQL computes. The model only reads the answer out loud — and a validator checks every
> digit against a query result before it reaches the user.**

---

## The problem the schema creates

The brief asks for vendor payouts, reconciliation status and categories. The schema has three
tables and none of those columns:

```mermaid
flowchart LR
  subgraph GIVEN["WHAT THE SCHEMA HAS"]
    B[("bank<br/>bank_code · bank_name")]
    A[("account<br/>account_id · entity_id<br/>account_number · balance")]
    T[("transaction<br/>date · type · amount<br/>description · refs")]
    B --> A --> T
  end
  subgraph ASKED["WHAT THE BRIEF ASKS FOR"]
    V["vendor list"]:::miss
    P["vendor payouts"]:::miss
    R["reconciliation status"]:::miss
    C["category"]:::miss
  end
  T -.->|"all four must be DERIVED<br/>from one free-text column"| ASKED
  classDef miss stroke:#b3283c,fill:transparent,stroke-width:2px,stroke-dasharray:4 3
```

`description` is the only place a counterparty name exists, and it arrives in nine different
narration formats with the name in a different position in each.

---

## Enrichment — done once, at ingestion

```mermaid
flowchart TD
  RAW[("transaction<br/>100,000 rows")] --> PARSE["1 · Parse narration<br/>one parser per rail, never one regex<br/><b>100.0% rail accuracy</b>"]
  PARSE --> CANON["2 · Canonicalise<br/>drop corporate tokens · fold abbreviations<br/>curated acronym map"]
  CANON --> CP[("counterparty<br/>186 vendors + people")]:::built
  CANON --> RECON["3 · Reconcile<br/>shared UTR + reference, opposite legs<br/>· or a ZBFL…PBL loan reference"]
  RECON --> ANOM["4 · Score anomalies<br/>median + MAD in LOG space<br/>per counterparty, |z| &gt; 4"]
  ANOM --> TE[("transaction_enriched<br/>rail · counterparty_id · category<br/>recon_status · anomaly_z")]:::built
  classDef built stroke:#0a6e78,fill:transparent,stroke-width:2px
```

Measured against a held-out answer key the engine never reads:

| | |
|---|---|
| rail classification | **100.000%** |
| counterparty grouping | **99.467%** (1 vendor split, 0 wrongly merged) |
| reconciliation status | **99.990%** |
| category (derived) | **96.227%** |
| unparsed narrations | **0** |

Parsing at ingestion rather than at query time is the point: afterwards, "how much did we pay
Blue Dart last month" is an ordinary indexed `GROUP BY`, so the model never reads a bank
narration string and never touches arithmetic.

---

## Answering a question

```mermaid
flowchart TD
  Q(["Question"]) --> PLAN["1 · PLAN<br/>keyword rules first<br/>model consulted only when unsure"]
  PLAN --> RES["2 · RESOLVE<br/>period → dates · name → counterparty_id<br/><b>deterministic, no model</b>"]
  RES --> G{"3 · GUARD"}
  G -->|"period outside coverage"| REF["Refuse<br/>+ state the real window"]:::stop
  G -->|"no such counterparty"| REF
  G -->|"name matches several"| ASK["Ask which one<br/>+ list every match"]:::warn
  G -->|"no such data (invoices, forecast, P&amp;L)"| REF
  G -->|"clear"| COMP["4 · COMPUTE<br/>parameterised SQL from a fixed<br/>intent registry — never generated SQL"]:::pri
  COMP --> EV[("Evidence<br/>value · unit · SQL · params · rows")]:::pri
  EV --> NAR["5 · NARRATE<br/>model writes prose with {{ev_N}} placeholders only"]
  NAR --> VAL{"Numeric validator<br/>is every digit from a query?"}
  VAL -->|"unsourced number"| RETRY["Reject · retry once"] --> NAR
  VAL -->|"clean"| OUT["Answer + breakdown table<br/>+ evidence ledger + CSV"]:::ok
  VAL -->|"fails twice"| TMPL["Deterministic template<br/>from the same evidence"]:::mut --> OUT
  classDef pri stroke:#0a6e78,fill:transparent,stroke-width:2px
  classDef ok stroke:#137a43,fill:transparent,stroke-width:2px
  classDef warn stroke:#a8690a,fill:transparent,stroke-width:2px
  classDef stop stroke:#b3283c,fill:transparent,stroke-width:2px
  classDef mut stroke:#5b6875,fill:transparent,stroke-width:2px
```

**Guards run before compute, not after.** An assistant that computes first and checks second
has already decided what the answer is, and the check becomes a formality.

---

## Why fabrication is impossible, not merely unlikely

The model is forbidden from writing a digit. It must emit `{{ev_3}}`, and the engine
substitutes the computed value. Anything else is rejected.

```mermaid
flowchart LR
  E[("Evidence<br/>ev_1 = Rs 695,439,580.02<br/>ev_2 = 3,814")] --> D["Model draft<br/>“We paid {{ev_1}} across {{ev_2}} payments.”"]
  D --> S["Substitute placeholders"]
  S --> V{"Every number token<br/>traceable to evidence,<br/>facts, or the table?"}
  V -->|"no"| X["REJECT"]:::stop
  V -->|"yes"| Y["Ship it"]:::ok
  X --> R["Retry once, then<br/>deterministic template"]:::mut
  classDef ok stroke:#137a43,fill:transparent,stroke-width:2px
  classDef stop stroke:#b3283c,fill:transparent,stroke-width:2px
  classDef mut stroke:#5b6875,fill:transparent,stroke-width:2px
```

Verified against five ways a model fabricates — all rejected:

| Draft | Verdict |
|---|---|
| `We paid Blue Dart Rs 4,100,000.00 across {{ev_2}} payments.` | invented a total → **rejected** |
| `We paid {{ev_1}} across {{ev_2}} payments (95% confident).` | invented a confidence → **rejected** |
| `We paid roughly Rs 3.4 million across {{ev_2}} payments.` | rounded to a new number → **rejected** |
| `We paid {{ev_1}} and {{ev_99}} more.` | cited evidence that does not exist → **rejected** |
| `We paid {{ev_1}} across 61 payments.` | invented a count → **rejected** |

`POST /demo/fabrication` runs this live, offline, with no model call — so it cannot flake
during a demo.

---

## System shape

```mermaid
flowchart LR
  subgraph D["DATA"]
    S[("SQLite / MySQL<br/>bank · account · transaction<br/>+ counterparty · transaction_enriched")]
  end
  subgraph E["ENGINE  (stdlib only)"]
    R["resolve.py<br/>dates · vendors"]
    I["intents.py<br/>13 parameterised queries"]
    N["narrate.py<br/>validator + template"]
    P["plan.py<br/>rules → model"]
  end
  subgraph SU["SURFACES"]
    U["Chat UI<br/>:8720"]
    API["JSON API<br/>/ask /export.csv"]
  end
  M[["Language model<br/>OPTIONAL"]]
  S --> I --> N --> API --> U
  P --> I
  R --> I
  M -.->|"plan (only when rules are unsure)"| P
  M -.->|"prose (validated)"| N
```

The only third-party dependency in the project is `openai`, and it is needed **only** for the
model. Every number, guardrail, table and CSV export works with a bare Python install.

---

## Model efficiency

| Path | Model calls per question |
|---|---|
| Keyword rules match (the common case) | **0** |
| Rules unsure, or a bare follow-up | 1 planning call (~200 output tokens, JSON) |
| Prose narration, when a key is configured | 1 call (~300 output tokens) |
| Fallback when no key is set | 0 — deterministic template |

The full 20-question answer key scores **20/20 with 0 model calls**. The model improves how
the sentence reads. It cannot change what the number is.
