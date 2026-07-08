"""Honesty guardrail for screening questions — the product's core promise.

Only auto-answers a question when the profile PROVES the answer; everything else
routes to the human (NEEDS_HUMAN). Two layers, because the AI is never trusted to
police itself:

  Layer 1 — known-question resolver (`resolve`): a small table of universal Yes/No
    questions, each answered from ONE explicit answer-bank field. A missing/empty
    field, or a question not in the table, -> NEEDS_HUMAN. Deterministic, no AI.

  Layer 2 — receipt check (`verify_ai_answer`): when an AI proposes an answer it
    must cite the answer-bank field it used. Code verifies that field exists AND that
    its value actually supports the Yes/No; if the citation is fake/empty or its value
    disagrees, the AI is OVERRIDDEN to NEEDS_HUMAN regardless of confidence.

Nothing here guesses. NEEDS_HUMAN is the safe, expected default. Same philosophy as
the resume fact-validator: the code demands a receipt, it doesn't trust the model.
"""
from __future__ import annotations

import re

NEEDS_HUMAN = "NEEDS_HUMAN"

# Human answer banks store Yes/No as strings, so bool("No") == True would flip a
# stored "No" into a confident "Yes". Normalize known false-strings before coercing,
# and refuse to guess on anything we can't read as a clean bool/known token.
_FALSE_TOKENS = {"no", "false", "0", "n", "f"}
_TRUE_TOKENS = {"yes", "true", "1", "y", "t"}


def _field_answer(value) -> str:
    """Map a backing field value to "Yes"/"No", or NEEDS_HUMAN if it isn't a clean
    truth value. bool() alone is wrong here: bool("No") is True. So real bools coerce
    directly, known yes/no tokens map explicitly, and anything ambiguous routes to the
    human rather than being guessed."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value in (None, ""):
        return NEEDS_HUMAN
    token = str(value).strip().lower()
    if token in _TRUE_TOKENS:
        return "Yes"
    if token in _FALSE_TOKENS:
        return "No"
    return NEEDS_HUMAN  # unreadable value -> human, never guess

# (regex over the lowercased question, answer-bank field that proves it).
# answer = "Yes" if bool(answers[field]) else "No".
# ONLY universal questions whose answer is a hard, reusable profile fact live here.
# Employer-specific ones (relatives at Citi, ex-KPMG, SGO/SCP referrals) are absent
# on purpose, so they correctly fall through to NEEDS_HUMAN.
# ponytail: a table, not an ontology — add a row when a universal question recurs
# across employers; anything unknown is already safe (routed to the human).
_RULES: list[tuple[str, str]] = [
    (r"authoriz.*work|work.*authoriz|verification.*(identity|work)|identity.*work", "work_authorized"),
    (r"sponsor", "needs_sponsorship"),
    (r"\b18\b|eighteen years", "is_over_18"),
    (r"veteran|armed forces|\bmilitary\b|national guard|\breserves\b", "is_veteran"),
]
_COMPILED = [(re.compile(pat), field) for pat, field in _RULES]


def resolve(question: str, answers: dict) -> str:
    """Yes/No if a KNOWN universal question is proven by the answer bank, else NEEDS_HUMAN.

    A known question whose backing field is missing or empty is flagged, not guessed
    — that is the enforcement: an empty required field always means a human answers.
    """
    q = " ".join(str(question).lower().split())
    for rx, field in _COMPILED:
        if rx.search(q):
            if field not in answers or answers[field] in (None, ""):
                return NEEDS_HUMAN
            return _field_answer(answers[field])
    return NEEDS_HUMAN  # unknown question -> human, never guess


def verify_ai_answer(answer: str, cited_field: str, answers: dict) -> str:
    """Layer 2: keep an AI's Yes/No only if it cited a real answer-bank field AND that
    field's VALUE actually supports the answer; otherwise override to NEEDS_HUMAN.

    A present-but-unchecked citation is not a receipt: a fabricating AI could return a
    confident "Yes" and cite any populated field (even one whose value is False). So
    the code re-derives the answer from the field and keeps the AI's answer only when
    the two agree — the AI never gets to assert a truth the field contradicts."""
    ans = str(answer).strip()
    if ans.upper() == NEEDS_HUMAN:
        return NEEDS_HUMAN
    field = str(cited_field or "").strip()
    if not field or field not in answers:
        return NEEDS_HUMAN
    proven = _field_answer(answers[field])
    if proven == NEEDS_HUMAN:
        return NEEDS_HUMAN
    return ans if ans.lower() == proven.lower() else NEEDS_HUMAN
