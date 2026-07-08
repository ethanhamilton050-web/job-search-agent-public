"""Production honesty pipeline — the product's core promise, run per question.

For each screening question this decides ONE of three fates, always erring toward
the human. It is a thin orchestrator over guardrail.py; the enforcement (never
trust the AI, demand a receipt) lives there and is REUSED, not reimplemented:

  Layer "profile" — guardrail.resolve auto-answers a universal question from a
    single, non-empty answer-bank field. We re-derive WHICH field it relied on so
    the caller has a source/receipt to show, then confirm resolve() agreed.
  Layer "ai+receipt" — no profile rule matched, so an AI proposes (answer, field);
    guardrail.verify_ai_answer keeps it only if that field is real and non-empty.
  Layer "human" — everything else: unknown question, no AI, or an AI answer whose
    receipt didn't check out. NEEDS_HUMAN is the safe, expected default.

Nothing here guesses. A fabricated AI answer (confident Yes/No, fake/empty
citation) can never reach the output — it is overridden to NEEDS_HUMAN by Layer 2.
"""
from __future__ import annotations

from . import guardrail
from .guardrail import NEEDS_HUMAN


def _profile_field(question: str) -> str:
    """The answer-bank field guardrail.resolve would match this question to, or "".

    Re-uses guardrail's own compiled rule table so the source we report can never
    drift from the logic that actually produced the answer.
    """
    q = " ".join(str(question).lower().split())
    for rx, field in guardrail._COMPILED:  # ponytail: reuse the table, don't copy it
        if rx.search(q):
            return field
    return ""


def answer_questions(questions, answers: dict, ai_answer_fn=None) -> list[dict]:
    """Resolve each question to {question, answer, source, layer}, honesty-first.

    ai_answer_fn(question) -> (answer, cited_field). It is only consulted when the
    profile has no rule for the question, and its output is always laundered through
    the Layer-2 receipt check before it is allowed to stand.
    """
    results: list[dict] = []
    for q in questions:
        field = _profile_field(q)
        resolved = guardrail.resolve(q, answers)
        if field and resolved != NEEDS_HUMAN:
            # Layer 1: a known field proved a Yes/No.
            results.append({"question": q, "answer": resolved,
                            "source": field, "layer": "profile"})
        elif ai_answer_fn is not None:
            ai_answer, cited_field = ai_answer_fn(q)
            checked = guardrail.verify_ai_answer(ai_answer, cited_field, answers)
            if checked == NEEDS_HUMAN:
                results.append({"question": q, "answer": NEEDS_HUMAN,
                                "source": "", "layer": "human"})
            else:
                results.append({"question": q, "answer": checked,
                                "source": str(cited_field or "").strip(),
                                "layer": "ai+receipt"})
        else:
            results.append({"question": q, "answer": NEEDS_HUMAN,
                            "source": "", "layer": "human"})
    return results


def summarize(results) -> dict:
    """Tally of a run: how many got a real answer vs. how many need the human."""
    needs_human = sum(1 for r in results if r["answer"] == NEEDS_HUMAN)
    return {"answered": len(results) - needs_human, "needs_human": needs_human}
