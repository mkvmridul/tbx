"""finassist/engine.py — question in, grounded answer out.

    engine = Engine()
    ans = engine.ask("How much did we pay Blue Dart last month?")

The pipeline, in order. Every step before narration is deterministic:

    1 PLAN      rules first, model only when the rules are unsure   (finassist.plan)
    2 RESOLVE   period and counterparty, in code                    (finassist.resolve)
    3 GUARD     refuse / ask, before any number is produced         (this file)
    4 COMPUTE   parameterised SQL, evidence recorded                (finassist.intents)
    5 NARRATE   model writes prose, validator rejects invented numbers (finassist.narrate)

Guards run BEFORE compute, not after. An assistant that computes first and checks later has
already decided what the answer is, and the check becomes a formality.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict

from finassist import db as _db
from finassist import plan as planner
from finassist.intents import REGISTRY, Ctx, Result
from finassist.narrate import narrate, demo_fabrication
from finassist.resolve import (Period, anchor, coverage, previous_period,
                               resolve_counterparty, resolve_period)


@dataclass
class Answer:
    question: str
    status: str = "ok"                 # ok | refused | clarify | empty
    confidence: str = "high"            # high | medium | model-validated | guarded | clarification
    answer: str = ""
    intent: str = ""
    headline: dict | None = None
    evidence: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    period: dict | None = None
    counterparty: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source: str = ""
    model: str = ""
    citations: list[str] = field(default_factory=list)
    plan_source: str = ""
    llm_calls: int = 0
    elapsed_ms: int = 0
    sources: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class Session:
    """What the assistant is allowed to remember between turns.

    Only three things carry over: the last counterparty, the last period and the last
    intent. Nothing else, and no free-text history is replayed into the compute path, so a
    follow-up can change WHICH rows are selected but can never change what a number means.
    """

    def __init__(self):
        self.counterparty: dict | None = None
        self.period: Period | None = None
        self.intent: str | None = None
        self.pending_candidates: list[dict] = []
        self.history: list[str] = []

    def remember(self, q: str, a: Answer, period: Period | None, cp: dict | None):
        self.history.append(f"user: {q}")
        if a.answer:
            self.history.append(f"assistant: {a.answer[:200]}")
        if period:
            self.period = period
        if cp:
            self.counterparty = cp
        if a.intent and a.status == "ok":
            self.intent = a.intent
        if a.status == "ok":
            self.pending_candidates = []
        self.history = self.history[-12:]


class Engine:
    def __init__(self, cfg: dict | None = None, use_llm: bool = True):
        self.cfg = cfg or {}
        self.use_llm = use_llm
        self.cx = _db.connect()
        self.db_label = _db.dsn_label()
        self._check_enriched()
        self.today = anchor(self.cx)
        self.cov_start, self.cov_end = coverage(self.cx)

    def _check_enriched(self):
        t = {r[0] for r in self.cx.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()")}
        missing = {"transaction_enriched", "counterparty"} - t
        if missing:
            raise RuntimeError(
                f"{self.db_label} is missing {', '.join(sorted(missing))}. "
                f"Run:  python -m finassist.enrich")

    # ---------------------------------------------------------------- guards

    def _out_of_range(self, p: Period) -> bool:
        return p.end < self.cov_start or p.start > self.cov_end

    def _refuse(self, q: str, text: str, intent: str = "unsupported", **kw) -> Answer:
        return Answer(question=q, status="refused", answer=text, intent=intent,
                      confidence="guarded", source="deterministic", model="guardrail", **kw)

    # ---------------------------------------------------------------- ask

    def ask(self, question: str, session: Session | None = None) -> Answer:
        session = session or Session()
        a = self._ask_once(question, session)
        if not self._retryable(a):
            return a
        b = self._ask_once(question, session, force_llm=True)
        b.llm_calls += a.llm_calls
        return b if b.status in ("ok", "clarify") else a

    def _retryable(self, a: Answer) -> bool:
        """Whether a second opinion from the model is worth the round trip.

        The five guardrails are deliberate answers, not failures: unsupported questions,
        periods outside the data, ambiguous vendors and vendors that do not exist must stay
        refused however the question is reworded.
        """
        if not self.use_llm or a.llm_calls or a.plan_source != "rules":
            return False
        if a.status == "empty":
            return True
        return (a.status == "refused"
                and a.intent != "unsupported"
                and not a.answer.startswith("No data for"))

    def _ask_once(self, question: str, session: Session | None = None,
                  force_llm: bool = False) -> Answer:
        t0 = time.perf_counter()
        session = session or Session()
        llm_calls = 0

        select_all = bool(session.pending_candidates and _selects_all(question))
        p = planner.make_plan(question, session.history, self.cfg, use_llm=self.use_llm,
                              force_llm=force_llm)
        if select_all:
            p = planner.Plan(session.intent or "spend_by_counterparty", {}, "inherited")
        if p.source == "llm":
            llm_calls += 1

        # --- follow-up: inherit last turn's subject ---------------------------
        if p.intent == "follow_up":
            p = planner.Plan(session.intent or "spend_total", {}, "inherited")
        elif p.confidence == "low" and session.intent and p.source == "rules":
            # The rules recognised no intent and were about to fall back to a whole-company
            # total. Mid-conversation that is the wrong default: "How many were there in
            # August?" after a reconciliation question is still about reconciliation.
            # Continuing the previous intent is recoverable -- the intent chip shows what was
            # answered -- whereas silently switching topic is not.
            p = planner.Plan(session.intent, p.slots, "inherited", vendor_text=p.vendor_text)
        elif p.confidence == "low" and p.source == "rules" and not _about_money(question):
            # First turn, rules unsure, no model answer to lean on, and the question does not
            # even mention money. Answering a whole-company total to "what is the weather" is
            # a wrong answer dressed as a right one. Say what we can do instead. (A vague but
            # money-shaped question -- "how much did we spend on vendor payouts" -- still gets
            # the spend_total default, which is the right reading of it.)
            p = planner.Plan("unsupported", source="rules", reason=(
                "I can answer questions about spend, vendors, categories, reconciliation and "
                "unusual payments — I didn't recognise that one."))

        # --- guard: the data cannot answer this at all -----------------------
        if p.intent == "unsupported":
            a = self._refuse(question, p.reason or "The data cannot answer that.")
            a.plan_source, a.llm_calls = p.source, llm_calls
            a.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            session.remember(question, a, None, None)
            return a

        # --- resolve the period ----------------------------------------------
        period = resolve_period(p.slots.get("period_text") or question, self.today)
        if period is None and p.intent in ("compare_periods",):
            period = session.period or resolve_period("last month", self.today)
        if period is None and session.period and _is_follow_up(question):
            period = session.period
        if period is None and select_all:
            period = session.period
        if p.intent == "spend_trend" and not resolve_period(p.slots.get("period_text") or question, self.today):
            # "show me that by month" asks to widen the view, not to keep the single month
            # the previous turn was scoped to. A one-month trend line is not a trend.
            period = None

        if period and self._out_of_range(period):
            a = self._refuse(
                question,
                f"No data for {period.label}. This dataset covers {self.cov_start} to "
                f"{self.cov_end}, so there are no transactions in that period.",
                intent=p.intent)
            a.period = period.as_dict()
            a.plan_source, a.llm_calls = p.source, llm_calls
            a.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            session.remember(question, a, None, None)
            return a

        # --- resolve the counterparty ----------------------------------------
        cp = None
        counterparties = session.pending_candidates if select_all else None
        if p.vendor_text:
            res = resolve_counterparty(self.cx, p.vendor_text)
            if res.ambiguous:
                names = [c["canonical_name"] for c in res.candidates]
                a = Answer(question=question, status="clarify", intent=p.intent,
                           candidates=[dict(c) for c in res.candidates],
                           confidence="clarification",
                           source="deterministic", model="guardrail",
                           answer=(f"“{p.vendor_text}” matches {len(names)} different "
                                   f"counterparties in the data: " + ", ".join(names) +
                                   ". Which one did you mean?"))
                a.plan_source, a.llm_calls = p.source, llm_calls
                a.elapsed_ms = int((time.perf_counter() - t0) * 1000)
                session.pending_candidates = [dict(c) for c in res.candidates]
                session.remember(question, a, period, None)
                return a
            if res.missing:
                a = self._refuse(
                    question,
                    f"No counterparty matching “{p.vendor_text}” appears in the data.",
                    intent=p.intent)
                a.plan_source, a.llm_calls = p.source, llm_calls
                a.elapsed_ms = int((time.perf_counter() - t0) * 1000)
                session.remember(question, a, period, None)
                return a
            cp = dict(res.value)
        elif p.intent in ("spend_by_counterparty", "counterparty_profile") and not select_all:
            cp = session.counterparty          # a follow-up about the same vendor
        elif (p.intent in ("compare_periods", "spend_trend", "anomalies")
              and session.counterparty and _is_follow_up(question)):
            # "How does that compare to the month before?" right after a question about one
            # vendor is still about that vendor. Without this the assistant silently answers
            # about total company spend instead, using the same words, and the user has no
            # way to tell the subject changed.
            cp = session.counterparty

        # --- guard: required slot still missing ------------------------------
        if err := planner.validate(p, {"counterparty": cp or counterparties, "period": period}):
            a = self._refuse(question,
                             f"I need a bit more to answer that: {err.replace('_', ' ')}.",
                             intent=p.intent)
            a.plan_source, a.llm_calls = p.source, llm_calls
            a.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            session.remember(question, a, period, cp)
            return a

        # --- compute ----------------------------------------------------------
        kwargs = {k: v for k, v in p.slots.items() if k != "period_text"}
        kwargs["period"] = period
        if cp:
            kwargs["counterparty"] = cp
        if counterparties:
            kwargs["counterparties"] = counterparties
        if p.intent == "compare_periods":
            kwargs["prior"] = previous_period(period)

        result: Result = REGISTRY[p.intent](Ctx(self.cx), **kwargs)

        # --- narrate ----------------------------------------------------------
        out = narrate(result, self.cfg, question)
        if out["source"].startswith("llm"):
            llm_calls += 1

        a = Answer(
            question=question,
            status="empty" if result.status == "empty" else "ok",
            answer=out["answer"], intent=result.intent, headline=result.headline,
            evidence=[e.as_dict() for e in result.evidence],
            columns=result.columns, rows=result.rows,
            period=result.period, counterparty=cp, notes=result.notes,
            source=out["source"], model=out["model"], citations=out["citations"],
            plan_source=p.source, llm_calls=llm_calls,
            elapsed_ms=int((time.perf_counter() - t0) * 1000))
        if out["source"].startswith("llm+"):
            a.confidence = "model-validated"
        elif p.source == "inherited":
            a.confidence = "medium"
        else:
            a.confidence = "high"
        a.sources = _sources(result, self.db_label, out["model"], p.source)
        session.remember(question, a, period, cp)
        return a

    # ------------------------------------------------------------ judge demo

    def fabrication_demo(self, question: str = "How much did we spend this month?") -> Answer:
        """Run the validator against a deliberately doctored answer, offline."""
        period = resolve_period(question, self.today) or resolve_period("last month", self.today)
        result = REGISTRY["spend_total"](Ctx(self.cx), period=period)
        out = demo_fabrication(result, question)
        return Answer(question=question, status="ok", answer=out["answer"],
                      intent="fabrication_demo", headline=result.headline,
                      evidence=[e.as_dict() for e in result.evidence],
                      columns=result.columns, rows=result.rows, period=result.period,
                      source=out["source"], model=out["model"], citations=out["citations"])

    def stats(self) -> dict:
        q = lambda s: self.cx.execute(s).fetchone()[0]
        return {
            "database": self.db_label,
            "source": _db.source_label(),
            "transactions": q("SELECT COUNT(*) FROM transaction_enriched"),
            "counterparties": q("SELECT COUNT(*) FROM counterparty"),
            "accounts": q("SELECT COUNT(*) FROM account"),
            "coverage_start": self.cov_start,
            "coverage_end": self.cov_end,
            "data_clock": self.today.isoformat(),
            "unmatched": q("SELECT COUNT(*) FROM transaction_enriched WHERE recon_status='unmatched'"),
            "intents": sorted(REGISTRY),
        }


_MONEY_WORDS = ("spend", "spent", "pay", "paid", "payment", "payout", "money", "amount",
                "total", "cost", "rs", "rupee", "transaction", "debit", "credit", "outflow",
                "inflow", "expense", "vendor", "supplier", "balance", "how much")


def _about_money(q: str) -> bool:
    ql = (q or "").lower()
    return any(w in ql for w in _MONEY_WORDS)


def _is_follow_up(q: str) -> bool:
    ql = (q or "").lower()
    return len(ql.split()) <= 9 and any(
        w in ql for w in ("that", "it", "them", "those", "same", "and ", "what about"))


def _selects_all(q: str) -> bool:
    ql = " ".join((q or "").lower().split())
    return ql in {"all", "all of them", "all options", "all matches", "everyone", "every option", "every match"}

def _sources(result, db_label: str, model: str, plan_source: str) -> dict:
    """Provenance for the answer: where the numbers came from and who wrote the sentence.

    Key names here are the contract with renderSources() in ui/index.html -- if one is
    dropped the panel renders `undefined`, or `NaN` for the numeric ones.
    """
    tables = sorted({t for e in result.evidence for t in re.findall(r"(?:FROM|JOIN)\s+`?([a-z_]+)`?", e.sql or "")})
    period = (result.period or {}).get("label") or "none"
    # Rows the breakdown table is built from, plus rows behind each scalar value.
    rows_returned = len(result.rows) + sum(int(e.row_count or 0) for e in result.evidence)
    return {
        "database": db_label,
        "tables": tables,
        "queries": len(result.evidence),
        "evidence_values": len(result.evidence),
        "rows_returned": rows_returned,
        "period": period,
        "interpreted_by": "rules" if plan_source != "llm" else "model",
        "narrated_by": model or "template",
        # kept for any client reading the older names
        "narration": model,
        "planner": plan_source,
    }
