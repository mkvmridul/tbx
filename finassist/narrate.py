"""finassist/narrate.py — the language model writes prose, never a number.

Adapted from the placeholder-validator pattern in clickathon-inmobi-2026 (agent/narrate.py),
tightened for money: the model must write every figure as a {{ev_N}} placeholder, we
substitute the computed value, and a validator REJECTS the draft if it contains any number
that no query produced. On rejection it retries once, then falls back to a deterministic
template built from the same evidence.

So a fabricated figure is not "unlikely" -- it cannot reach the user. That matters more here
than in the ad-metrics original: a wrong number in a finance answer is a liability, not a
cosmetic bug.

    out = narrate(result, cfg)
    out["source"] in {"llm+validator", "deterministic", "deterministic(validator-rejected)"}
"""
from __future__ import annotations

import os
import re

# Numbers as they appear in prose: 1234, 1,234.56, .5, 16.0
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?|\.\d+")
PLACEHOLDER = re.compile(r"\{\{\s*(ev_\d+)\s*\}\}")

PROMPT = """You are a finance analyst answering a colleague's question about their own transaction data.

Write 1-3 short sentences. Say what the figure is, over what period, and for which counterparty
or category if one applies. If a NOTE is given, reflect its caveat in your wording.

HARD RULES:
- Every number MUST be written as its {{ev_N}} placeholder, exactly as shown, double braces
  included. Never write a digit of your own -- not a total, not a count, not a percentage,
  not a rounded or "approximately" version.
- Do not add any figure that is not in the EVIDENCE list.
- Do not explain what the numbers mean for the business, do not speculate about causes, and
  do not recommend an action. Report only.
- Dates and counterparty names are written normally, not as placeholders.
- Plain and direct. No preamble, no sign-off.

QUESTION:
%%QUESTION%%

FACTS:
%%FACTS%%

EVIDENCE (use these exact placeholders for every number):
%%EVIDENCE%%

NOTE:
%%NOTES%%

Answer:"""


def _fmt(value, unit: str) -> str:
    """How a computed value is rendered in prose."""
    if unit == "INR":
        return f"Rs {value:,.2f}"
    if unit == "count":
        return f"{int(value):,}"
    if unit == "percent":
        return f"{value:+.1f}%"
    return str(value)


def _ucfirst(s: str) -> str:
    """Raise the first character only, leaving acronyms and vendor names intact."""
    return s[:1].upper() + s[1:] if s else s


def _facts_block(result) -> str:
    return "\n".join(f"- {f}" for f in result.facts) or "- (none)"


def _evidence_block(result) -> str:
    return "\n".join(f"  {{{{{e.id}}}}} = {_fmt(e.value, e.unit)}   ({e.label})"
                     for e in result.evidence)


def _notes_block(result) -> str:
    return "\n".join(f"- {n}" for n in result.notes) or "- (none)"


def _allowed(result, question: str) -> set[str]:
    """Every numeric token the answer is permitted to contain.

    Sources: the resolved evidence values, the facts block (which carries dates, period
    bounds and the ranking line), and the user's own question -- a user who asks about "Q2
    2026" may see 2026 echoed back. Anything else is invented.
    """
    pool = [_fmt(e.value, e.unit) for e in result.evidence]
    pool.append(_facts_block(result))
    pool.append(_notes_block(result))
    pool.append(question or "")
    if result.period:
        pool += [result.period.get("start", ""), result.period.get("end", ""),
                 result.period.get("label", "")]
    for c in result.columns:
        pool.append(str(c))
    for row in result.rows[:400]:                 # values the user can see in the table
        pool.extend(str(v) for v in row)
    allowed = set()
    for chunk in pool:
        for m in NUM.findall(str(chunk)):
            allowed.add(m.replace(",", ""))
    return allowed


def _resolve_and_validate(draft: str, result, question: str) -> str | None:
    """Substitute placeholders, then reject the draft if any number is unsourced."""
    evmap = {e.id: _fmt(e.value, e.unit) for e in result.evidence}
    out = draft
    for eid, shown in evmap.items():
        out = out.replace("{{" + eid + "}}", shown).replace("{{ " + eid + " }}", shown)

    # A leftover placeholder means the model cited evidence that does not exist.
    if "{{" in out or "}}" in out or PLACEHOLDER.search(out):
        return None

    allowed = _allowed(result, question)
    for tok in NUM.findall(out):
        bare = tok.replace(",", "")
        if bare in allowed:
            continue
        # tolerate a trailing-zero difference only ("16" vs "16.0"), never a new value
        try:
            if any(abs(float(bare) - float(a)) < 1e-9 for a in allowed):
                continue
        except ValueError:
            pass
        return None
    return out.strip()


def _template(result, question: str) -> str:
    """Deterministic answer, always grounded. Used when no model is configured, the model
    is unreachable, or it kept writing numbers it was told not to write."""
    h = result.headline
    period = (result.period or {}).get("label")
    where = f" in {period}" if period else ""

    if result.status == "empty":
        return f"No matching transactions{where}." if where else "No matching transactions."
    if not h:
        return f"{len(result.rows):,} rows returned{where}."

    val = _fmt(h["value"], h["unit"])
    # str.capitalize() lowercases the rest of the string, which mangles counterparty names
    # ("BLUE DART EXPRESS LIMITED" -> "Blue dart express limited"). Only the first character
    # is raised.
    lead = f"{_ucfirst(h['label'])}{where}: {val}."

    extras = []
    for e in result.evidence:
        if e.id == h.get("evidence_id") or e.unit not in ("INR", "count", "percent"):
            continue
        if e.unit == "count" and int(e.value) == 0:
            continue
        extras.append(f"{e.label} {_fmt(e.value, e.unit)}")
        if len(extras) == 2:
            break
    if extras:
        lead += " " + _ucfirst("; ".join(extras)) + "."
    return lead


def narrate(result, cfg: dict | None = None, question: str = "",
            model: str | None = None) -> dict:
    """Return {answer, source, model, citations, rejected}."""
    cfg = cfg or {}
    if result.status in ("empty", "unsupported"):
        # Nothing was computed, so there is nothing for a model to narrate. Answering these
        # from a template is not a fallback -- it is the correct behaviour, and it is what
        # keeps an empty result from becoming an invented figure.
        return {"answer": _template(result, question), "source": "deterministic",
                "model": "template", "citations": [], "rejected": None}

    key = cfg.get("LLM_API_KEY") or cfg.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY") \
        or os.environ.get("OPENAI_API_KEY")
    model = model or cfg.get("LLM_MODEL") or os.environ.get("LLM_MODEL") or "gpt-4o-mini"
    base = cfg.get("LLM_BASE_URL") or os.environ.get("LLM_BASE_URL")

    rejected_model = None
    if key:
        prompt = (PROMPT
                  .replace("%%QUESTION%%", question or "(not supplied)")
                  .replace("%%FACTS%%", _facts_block(result))
                  .replace("%%EVIDENCE%%", _evidence_block(result))
                  .replace("%%NOTES%%", _notes_block(result)))
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=base) if base else OpenAI(api_key=key)
            for _ in range(2):                       # one retry on a rejected draft
                r = client.chat.completions.create(
                    model=model, temperature=0, max_tokens=300,
                    messages=[{"role": "user", "content": prompt}])
                draft = r.choices[0].message.content or ""
                final = _resolve_and_validate(draft, result, question)
                if final:
                    return {"answer": final, "source": "llm+validator", "model": model,
                            "citations": sorted(set(PLACEHOLDER.findall(draft))),
                            "rejected": None}
                rejected_model = model
        except Exception:
            pass                                     # any transport failure -> template

    return {"answer": _template(result, question),
            "source": "deterministic(validator-rejected)" if rejected_model else "deterministic",
            "model": f"validator-rejected:{rejected_model}" if rejected_model else "template",
            "citations": [e.id for e in result.evidence], "rejected": rejected_model}


def demo_fabrication(result, question: str = "") -> dict:
    """The judge demo, run offline so it cannot flake on stage.

    Takes the grounded answer, doctors it with a figure no query produced, and runs it
    through the SAME validator that gates every model reply -- then shows the rejection and
    the clean replacement. Borrowed from the clickathon repo's demo_fabrication(); no model
    call is involved.
    """
    clean = _template(result, question)
    fake = "Confidence in this figure: 99.97%, and roughly Rs 12,40,000 is still pending."
    verdict = _resolve_and_validate(clean + " " + fake, result, question)
    allowed = _allowed(result, question)
    caught = sorted({t.replace(",", "") for t in NUM.findall(fake)} - allowed)
    return {
        "answer": ("FABRICATION REJECTED\n"
                   f"The draft tried to state: “{fake}”\n"
                   f"Validator: {', '.join(caught)} appear in no query result, so the draft was "
                   "discarded before it reached you. Every number below resolves to a SQL query "
                   "you can rerun.\n\nGrounded answer:\n" + clean),
        "source": "deterministic", "model": "fabrication-demo",
        "citations": [e.id for e in result.evidence],
        "rejected": "demo", "validator_passed": verdict is not None,
    }
