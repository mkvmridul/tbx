"""finassist/plan.py — decide WHICH question is being asked, never what the answer is.

The model's only job is to map free text onto one intent from finassist.intents.REGISTRY
and fill that intent's slots. It does not write SQL and it does not compute anything.

Two consequences worth stating plainly:
  * A wrong intent is visible. The user sees the breakdown table and the period label next
    to the answer, so "that's not what I asked" is obvious.
  * A wrong generated query is invisible. Which is why generated SQL is not used here.

A rule-based planner runs first and the model is consulted only when the rules are not
confident. That keeps the common questions free of an API call, which is what the brief's
model-efficiency criterion is measuring.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from finassist.intents import REGISTRY, SLOTS, REQUIRED

# Questions the data cannot answer, matched before any intent is chosen. Each carries the
# reason the assistant should give, because "I don't know" is far less useful than
# "this dataset has bank transactions, not invoices".
UNSUPPORTED = [
    (r"\b(gst|tds|vat|tax)\s+(component|breakup|break-?down|split|portion)",
     "The data has no tax breakdown. It holds bank transactions only, with no invoice or line-item detail."),
    (r"\b(invoice|bill|purchase order|po number|line item|gl code|ledger code|journal)",
     "The data has no invoices, purchase orders or ledger codes. It holds bank transactions only."),
    (r"\b(will|forecast|predict|projection|expect(ed)?\s+to|next (month|quarter|year))\b",
     "The data ends at the last recorded transaction and contains no forecast."),
    (r"\b(profit|margin|ebitda|p\s*&\s*l|balance sheet|cash flow statement|revenue recognition)",
     "The data has no profit-and-loss or balance-sheet figures, only bank transactions and balances."),
    (r"\b(budget|variance vs budget|plan vs actual)",
     "The data contains no budget to compare against."),
    (r"\b(customer|client)s?\b(?=.*\b(revenue|largest|top|biggest|most)\b)|"
     r"\b(revenue|largest|top|biggest|most)\b(?=.*\b(customer|client)s?\b)",
     "The data does not label counterparties as customers or suppliers, so it cannot rank customers. "
     "It can rank counterparties by money received."),
    (r"\b(employee|headcount|payroll register|salary slip)",
     "The data has no employee or payroll records. Payments to individuals appear only as bank transactions."),
]

# Intent keyword rules, most specific first. `needs_vendor` means the rule only applies when
# a counterparty was actually resolved, so "top vendors" does not become a single-vendor query.
RULES = [
    ("recon_summary",        r"\b(reconcil\w*|matched|matching)\b.*\b(summary|status|overview|breakdown|split|how many)\b|\bhow much is (un)?reconciled\b|\bwhat(?:'s| is) (?:the )?(reconciliation|matching) status\b"),
    ("unreconciled",         r"\b(unreconciled|un-?reconciled|not reconciled|outstanding|unmatched|pending match|still open|not matched)\b"),
    ("compare_periods",      r"\b(compare|comparison|versus|vs\.?|against|difference)\b|\bmonth before\b|\bprevious (month|quarter|year)\b|\bhow does that compare\b|\bchange (from|vs)\b|\b(up|down) from last\b|\bmore or less than last\b"),
    ("anomalies",            r"\b(unusual|anomal\w*|outlier|suspicious|spike|abnormal|unexpected|stand ?out|out of the ordinary|red flag|irregular|one-off|odd payment)\b"),
    ("spend_trend",          r"\b(trend|over time|by month|monthly|month by month|each month|per month|history|month-wise|monthwise|timeline|how has .* changed)\b"),
    ("spend_by_category",    r"\b(categor\w+|what kind of|types? of (spend|expense)|where (is|does) (the |our )?money go|where did (we|our company) spend (the )?(more|most)|how did (we|our company) spend|what did we spend (the )?(most|money) on|spend(?:ing)? breakdown|breakdown of (our )?spend(?:ing)?|expense breakdown|outgoings? (?:breakdown|by (type|category))|cash burn|spend split|expense split|spending allocation|where did .* go)\b"),
    ("spend_by_bank",         r"\bwhich bank\b|\bby bank\b|\bper bank\b|\bbank(?:s)?\b.*\b(most|largest|biggest|highest|breakdown|split|spend)\b|\b(most|largest|biggest|highest)\b.*\bbank(?:s)?\b"),
    ("top_counterparties",   r"\b(top|biggest|largest|highest|most|major)\b.*\b(vendor|supplier|counterpart\w+|payee|recipient|expense|payment|spend|outgoing)\b|\bwho (do|did) we pay the most\b|\bwho are our (top|biggest|largest|major)\b|\b(which|show|list|give me)\b.*\b(vendors?|suppliers?|counterpart\w+|payees?|recipients?)\b.*\b(each|all|every|most|highest|largest|paid|spend|spent|money|received|rank)\b|\b(each|all|every)\s+(?:the\s+)?(?:vendors?|suppliers?|counterpart\w+|payees?)\b|\bwhich\s+(vendor|supplier|payee|recipient)\b.*\b(received|got|was paid)\b.*\b(most|highest|largest)\b|\bwho got paid the most\b|\bwho received the most\b|\b(where|what)\b.*\b(should|can)\b.*\b(limit|reduce|cut|lower|control)\b.*\b(spend|spending|expenses?|outgoings?)\b"),
    ("account_balances",     r"\bacc(?:ount|t)?\.?\s*(?:num(?:ber)?s?|nos?\.?)\b|\ba/c\b|\b(account number|account no\.?|bank account|balance|balances|how much (do we|is) (have|held|in the account))\b"),
    ("counterparty_profile", r"\b(who is|what is|tell me about|profile of|details (on|about)|do we (know|deal with))\b"),
    ("transaction_search",   r"\b(utr|reference|ref no|ref number|transaction id|find the (payment|transaction)|look ?up)\b"),
]

REF_TOKEN = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b")

# Spoken bank names -> the bank_code actually stored in the `bank` table.
BANK_ALIASES = {
    "hdfc": "HDFC", "icici": "ICIC", "icic": "ICIC", "sbi": "SBIN", "sbin": "SBIN",
    "state bank": "SBIN", "axis": "UTIB", "utib": "UTIB", "kotak": "KKBK", "kkbk": "KKBK",
    "canara": "CNRB", "cnrb": "CNRB", "union bank": "UBIN", "ubin": "UBIN",
    "au small": "AUBL", "aubl": "AUBL", "tamilnad": "TMBL", "tmbl": "TMBL",
    "rbl": "RATN", "ratn": "RATN",
}


def _bank_code(question: str) -> str | None:
    q = (question or "").lower()
    for alias in sorted(BANK_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", q):
            return BANK_ALIASES[alias]
    return None

# Users misspell "transactions" constantly (trancations, transcations, trasactions), and a
# missed match here silently rewrites the question into a whole-company spend total.
TXN_WORD = re.compile(r"\b(?:t(?:ra|ar|r)[a-z]*(?:ction|cation|sction|ation)s?|txns?|trxns?)\b", re.I)


def _mentions_transactions(q: str) -> bool:
    return bool(TXN_WORD.search(q or ""))

# Phrases that sit where a vendor name would sit but name no vendor. Without this list
# "spend on vendor payouts" resolves "vendor payouts" as a counterparty, finds nothing, and
# the assistant reports that a vendor the user never named does not exist.
NOT_A_VENDOR = {
    "us", "them", "it", "that", "this", "these", "those", "all", "everything", "anything",
    "vendor", "vendors", "supplier", "suppliers", "payout", "payouts", "vendor payout",
    "vendor payouts", "payment", "payments", "transaction", "transactions", "spend",
    "spending", "expenses", "expense", "money", "cash", "total", "totals", "everyone",
    "anyone", "someone", "customer", "customers", "client", "clients", "month", "months",
    "the month", "last month", "this month", "prev month", "previous month", "prior month",
    "the year", "by month", "each month",
    "per month", "the most", "the largest", "our largest customer", "our biggest customer",
    "reconciliation", "reconciled", "balance", "balances", "account", "accounts",
    "our money", "our cash", "money", "cash", "each vendor", "each supplier", "all vendors", "all suppliers",
    "which vendors", "which suppliers", "every vendor", "every supplier",
}

# Words that mean the captured phrase is an intent keyword, not a name.
VENDOR_STOPWORDS = {"month", "months", "monthly", "week", "year", "quarter", "category",
                    "categories", "trend", "time", "date", "day", "days", "most", "top",
                    "largest", "biggest", "highest", "customer", "customers", "client",
                    "before", "after", "ago", "prior", "previous", "earlier", "later",
                    "period", "periods", "same", "one", "more", "less", "much"}

CATEGORY_WORDS = {
    "rent": "rent", "utilities": "utilities", "utility": "utilities", "electricity": "utilities",
    "power": "utilities", "logistics": "logistics", "shipping": "logistics",
    "freight": "logistics", "courier": "logistics", "software": "software",
    "saas": "software", "cloud": "software", "marketing": "marketing",
    "advertising": "marketing", "ads": "marketing", "travel": "travel", "flights": "travel",
    "hotels": "travel", "insurance": "insurance", "tax": "tax", "taxes": "tax", "gst": "tax",
    "professional services": "professional_services", "consulting": "professional_services",
    "legal": "professional_services", "audit": "professional_services",
    "inventory": "inventory", "stock": "inventory", "goods": "inventory",
}


@dataclass
class Plan:
    intent: str
    slots: dict = field(default_factory=dict)
    source: str = "rules"                 # rules | llm | llm-invalid->rules | inherited
    reason: str = ""                      # set when intent == "unsupported"
    confidence: str = "high"              # high | low
    vendor_text: str | None = None        # the phrase believed to name a counterparty


def check_unsupported(question: str) -> str | None:
    q = (question or "").lower()
    for pat, why in UNSUPPORTED:
        if re.search(pat, q):
            return why
    return None


def _vendor_phrase(question: str) -> str | None:
    """Pull the phrase that names a counterparty out of the question.

    Deliberately crude: it hands a candidate string to resolve_counterparty(), which is the
    component that actually decides whether it names one vendor, several, or none. Guessing
    is safe here precisely because the guess is checked against the counterparty table.
    """
    q = question or ""
    # A name ends at a boundary word ("in August"), at punctuation, or at end of string.
    # The boundary alternation must allow punctuation WITHOUT a preceding space, or
    # "spend on Selection?" never terminates and the whole pattern fails to match.
    boundary = r"in|on|at|during|last|this|next|over|between|since|from|by|for|and|the|a|an"
    tail = rf"(?=\s+(?:{boundary})\b|\s*[,.?!]|$)"
    # The name must not START with one of those boundary words either, or "spend in August
    # 2026" captures "in August 2026" and the assistant reports that no such vendor exists.
    name = rf"((?!(?:{boundary})\b)[A-Za-z][\w&.\- ]{{1,45}}?)"
    for pat in (rf"\b(?:who is|tell me about|profile of|details (?:on|about))\s+{name}{tail}",
                rf"\b(?:to|with|for)\s+{name}{tail}",
                rf"\b(?:paid|pay|spend|spent)\s+(?:on\s+|with\s+)?{name}{tail}"):
        if m := re.search(pat, q, re.I):
            cand = " ".join(m.group(1).split()).strip(" ,.?!")
            low = cand.lower()
            if not cand or low in NOT_A_VENDOR:
                continue
            # reject a phrase built only from intent keywords ("by month", "the most")
            words = [w for w in re.findall(r"[a-z]+", low) if w not in {"the", "our", "a", "an"}]
            if not words or all(w in VENDOR_STOPWORDS for w in words):
                continue
            # A phrase carrying a 4-digit year or a month name is a date, not a vendor.
            if re.search(r"\b(?:19|20)\d{2}\b", cand) or re.search(
                    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b", low):
                continue
            return cand
    return None


def _is_whole_category(phrase: str) -> bool:
    """True only if the entire phrase names a category, e.g. "logistics", "rent"."""
    return (phrase or "").strip().lower() in CATEGORY_WORDS


def _category(question: str) -> str | None:
    """Name a spend category if the question clearly states one."""
    q = (question or "").lower()
    for phrase, cat in sorted(CATEGORY_WORDS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(phrase)}\b", q):
            return cat
    return None


def plan_by_rules(question: str) -> Plan:
    q = (question or "").strip()
    if why := check_unsupported(q):
        return Plan("unsupported", reason=why, source="rules")

    vendor = _vendor_phrase(q)
    # A category name sits exactly where a vendor name sits ("spend on logistics"), so a
    # phrase that IS a category is treated as one. Otherwise the assistant hunts for a
    # supplier called "logistics", finds none, and reports that instead of the answer.
    # Only when the phrase IS a category, not when it merely contains one.
    # "Tata Power Company Limited" contains "power" and "Razorpay Software Pvt Ltd" contains
    # "software"; both are vendors. Substring matching here turned two vendor questions into
    # whole-company category totals that were wrong by an order of magnitude.
    if vendor and _is_whole_category(vendor):
        return Plan("spend_total", {"category": CATEGORY_WORDS[vendor.strip().lower()]}, "rules")
    if m := REF_TOKEN.search(q):
        return Plan("transaction_search", {"reference": m.group(0)}, "rules", vendor_text=vendor)

    if re.search(r"\b(where|what)\b.*\b(should|can)\b.*\b(limit|reduce|cut|lower|control)\b.*\b(spend|spending|expenses?|outgoings?)\b", q, re.I):
        return Plan("spend_by_category", {}, "rules")

    limit = None
    if m := re.search(r"\b(?:top|first|largest|biggest|highest)\s+(\d{1,3})\b", q, re.I):
        limit = int(m.group(1))
    elif m := re.search(r"\b(\d{1,3})\s+(?:top|largest|biggest|highest)\b", q, re.I):
        limit = int(m.group(1))
    elif re.search(r"\b(all|each|every)\s+(?:the\s+)?(?:vendors?|suppliers?|counterparties?)\b", q, re.I):
        limit = 200
    elif m := re.search(r"\b(?:of|for)\s+(\d{1,3})\s+(?:people|persons|accounts?|account holders?)\b", q, re.I):
        limit = int(m.group(1))
    elif m := re.search(rf"\b(?:prev|previous|last|latest|most recent)\s+(\d{{1,3}})\s+{TXN_WORD.pattern}", q, re.I):
        limit = int(m.group(1))

    for intent, pat in RULES:
        if re.search(pat, q, re.I):
            # "who is X" only makes sense with an X; without one fall through.
            if intent == "counterparty_profile" and not vendor:
                continue
            slots = {"limit": limit} if limit and "limit" in SLOTS[intent] else {}
            if intent == "account_balances":
                if _mentions_transactions(q):
                    slots["recent"] = True
                    slots["by_amount"] = bool(re.search(
                        r"\b(highest|largest|biggest|top|maximum|max)\b|\bby amount\b", q, re.I))
                    slots.setdefault("limit", 10)
                if code := _bank_code(q):
                    slots["bank"] = code
            # An intent with no counterparty slot must not carry a vendor guess: the engine
            # would try to resolve it and refuse the question over a phrase like "highest amount".
            keep_vendor = vendor if "counterparty" in SLOTS[intent] else None
            return Plan(intent, slots, "rules", vendor_text=keep_vendor)

    if vendor:
        return Plan("spend_by_counterparty", {}, "rules", vendor_text=vendor)

    if cat := _category(q):
        return Plan("spend_total", {"category": cat}, "rules")

    # Common company-wide totals stay deterministic, but only after category and other
    # specific intents have first claim on the wording.
    if re.search(
            r"\b(how much|what was|what is|total|overall|in all)\b.*\b(spend|spent|spending|paid|outgoings?|expenses?|payments?|cash)\b|"
            r"\b(spend|spending|expenses?|outgoings?|payments?)\b.*\b(total|overall|altogether|in all)\b",
            q, re.I):
        return Plan("spend_total", {}, "rules")

    # Nothing matched. spend_total is the safe default, but flag low confidence so the
    # engine can consult the model instead of quietly answering a different question.
    return Plan("spend_total", {}, "rules", confidence="low", vendor_text=vendor)


LLM_PROMPT = """Map the user's question onto ONE intent and its slots. Reply with JSON only.

INTENTS (slots in brackets):
%%INTENTS%%

Slot meanings:
  counterparty  the vendor/person named in the question, copied verbatim from it. null if none.
  period        the time phrase copied verbatim ("last month", "August 2026", "Q2"). null if none.
  category      one of: utilities logistics software rent marketing travel insurance tax
                professional_services inventory. null unless the question clearly names one.
  reference     a transaction/UTR/loan reference from the question. null if none.
  limit         an integer if the question asks for "top 5" etc. null otherwise.

Rules:
  - Copy slot text VERBATIM from the question. Do not resolve dates. Do not compute anything.
  - If the question asks about invoices, tax breakdowns, budgets, forecasts, profit or
    employees, reply {"intent":"unsupported","reason":"<one short sentence>"}.
  - If the question is a follow-up that omits its subject ("and the month before?"), reply
    {"intent":"follow_up"} and leave slots null.

CONVERSATION SO FAR:
%%HISTORY%%

QUESTION: %%QUESTION%%

JSON:"""


def plan_by_llm(question: str, history: list[str], cfg: dict | None = None) -> Plan | None:
    cfg = cfg or {}
    key = cfg.get("LLM_API_KEY") or cfg.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY") \
        or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    model = cfg.get("LLM_MODEL") or os.environ.get("LLM_MODEL") or "gpt-4o-mini"
    base = cfg.get("LLM_BASE_URL") or os.environ.get("LLM_BASE_URL")
    intents = "\n".join(f"  {k}  [{', '.join(SLOTS[k])}]" for k in REGISTRY)
    prompt = (LLM_PROMPT.replace("%%INTENTS%%", intents)
              .replace("%%HISTORY%%", "\n".join(history[-6:]) or "(new conversation)")
              .replace("%%QUESTION%%", question))
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=base) if base else OpenAI(api_key=key)
        msgs = [{"role": "user", "content": prompt}]
        try:
            r = client.chat.completions.create(
                model=model, temperature=0, max_tokens=400,
                response_format={"type": "json_object"}, messages=msgs)
        except Exception:
            # Some OpenAI-compatible endpoints reject response_format; the prompt already
            # demands JSON, so retry plain and extract the first object from the text.
            r = client.chat.completions.create(
                model=model, temperature=0, max_tokens=400, messages=msgs)
        content = r.choices[0].message.content or ""
        try:
            raw = json.loads(content)
        except ValueError:
            m = re.search(r"\{.*\}", content, re.S)
            raw = json.loads(m.group(0)) if m else {}
    except Exception:
        return None

    intent = str(raw.get("intent") or "").strip()
    if intent == "unsupported":
        return Plan("unsupported", reason=str(raw.get("reason") or "").strip()
                    or "The data cannot answer that.", source="llm")
    if intent == "follow_up":
        return Plan("follow_up", source="llm")
    if intent not in REGISTRY:
        return None                                   # unknown intent -> caller keeps the rules plan

    slots, vendor = {}, None
    # Some models nest the slots ({"intent": ..., "slots": {...}}); accept both shapes.
    src = raw.get("slots") if isinstance(raw.get("slots"), dict) else raw
    for k in SLOTS[intent]:
        v = src.get(k, raw.get(k))
        if v in (None, "", "null"):
            continue
        if k == "counterparty":
            vendor = str(v)
        elif k == "limit":
            try:
                slots["limit"] = int(v)
            except (TypeError, ValueError):
                pass
        elif k == "period":
            slots["period_text"] = str(v)
        else:
            slots[k] = str(v)
    return Plan(intent, slots, "llm", vendor_text=vendor or src.get("counterparty") or raw.get("counterparty"))


def make_plan(question: str, history: list[str] | None = None, cfg: dict | None = None,
              use_llm: bool = True, force_llm: bool = False) -> Plan:
    """Rules first; the model is asked only when the rules are unsure or see a follow-up.

    With LLM_PLANNER_FIRST=1 the order flips: refusal patterns and reference tokens stay
    regex (precise and free), then the model classifies; rules are the offline fallback."""
    rules = plan_by_rules(question)
    if rules.intent == "unsupported":
        return rules
    if use_llm and os.environ.get("LLM_PLANNER_FIRST", "").strip().lower() in ("1", "true", "yes"):
        if rules.intent == "transaction_search" and rules.slots.get("reference"):
            return rules                              # a UTR/reference token is a regex job
        llm = plan_by_llm(question, history or [], cfg)
        if llm is not None:
            return llm                                # includes follow_up / unsupported
        return rules                                  # model unreachable -> rules
    looks_like_follow_up = len((question or "").split()) <= 8 and not rules.vendor_text
    if not use_llm or (not force_llm and rules.confidence == "high" and not looks_like_follow_up):
        return rules
    llm = plan_by_llm(question, history or [], cfg)
    if llm is None:
        return rules
    # A model that drops the vendor turns "how much did we pay <unknown vendor>" into a
    # whole-company total, which is worse than the refusal it was meant to rescue.
    if (force_llm and rules.vendor_text and not llm.vendor_text
            and llm.intent in ("spend_by_counterparty", "counterparty_profile")):
        llm.vendor_text = rules.vendor_text
    if llm.intent == "follow_up":
        return Plan("follow_up", source="llm")
    return llm


def validate(plan: Plan, resolved: dict) -> str | None:
    """Return an error string if the plan cannot be executed as filled."""
    if plan.intent in ("unsupported", "follow_up"):
        return None
    for req in REQUIRED.get(plan.intent, []):
        if not resolved.get(req):
            return f"{plan.intent} needs a {req}"
    return None
