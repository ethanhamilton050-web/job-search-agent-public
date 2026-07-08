"""Deterministic replay planner — the runtime the product plan describes.

Given a cached field-map (from `fieldmap.map_form`) and the answer bank, decide
what to fill on a Workday page with NO AI and NO browser: a pure function that
turns (descriptors, mapping, answers) into an ordered PLAN. The AI mapped the page
once; from then on filling is just table lookup, so it must be replayable offline
and never phone home.

This produces a PLAN ONLY. It is deliberately NOT wired into the live browser —
executing the plan (typing into fields, clicking dropdowns) is a later host step
that owns the page. Keeping the decision layer pure is what makes it testable
without a browser or a model, and is why the honesty promise holds: a plan can be
inspected before a single keystroke.

Honesty rule (same spirit as guardrail.py): a value is only ever taken from a
same-named, non-empty answer-bank key. If the intent maps to no key, or the key is
empty, the field becomes action "needs_human" with value None — NEVER a guess.
"""
from __future__ import annotations

from ..guardrail import NEEDS_HUMAN

# strategy (from the field-map) -> what the executor will do with the field.
# Anything not listed is unknown-shape and routed to a human rather than guessed.
_STRATEGY_ACTION = {
    "text": "fill",
    "select": "select",
    "radio": "radio",
    "checkbox": "checkbox",
    "file": "fill",      # file upload is still a "put this value in" action for the executor
    "ignore": "skip",
}


def _value_for(intent: str, answers: dict):
    """The answer-bank value for an intent, or None if there's no non-empty match.

    Only a same-named key counts — we never map intent->some other field, because
    inventing a mapping is inventing a value. Empty string / None / empty list all
    count as "no answer" so the field routes to a human.
    """
    val = answers.get(intent)
    if val in (None, "", [], {}):
        return None
    return val


def plan_actions(descriptors: list[str], mapping: list[dict], answers: dict) -> list[dict]:
    """Turn a cached field-map into an ordered fill plan (no AI, no browser).

    `mapping` is fieldmap output: [{"i": 0, "intent": "first_name", "strategy": "text"}, ...].
    Returns one dict per mapped field, in the mapping's order:
        {index, intent, strategy, value, action}
    where action is fill | select | radio | checkbox | skip | needs_human.

    - intent "ignore" (page chrome) -> skip, no value.
    - a strategy with a known action + a non-empty same-named answer -> that action.
    - no matching/non-empty answer, or an unknown strategy -> needs_human, value None.
    """
    plan = []
    for row in mapping:
        index = row.get("i")
        intent = str(row.get("intent", "")).strip()
        strategy = str(row.get("strategy", "")).strip()

        # Page chrome: nothing to fill regardless of any stray answer-bank key.
        if intent == "ignore" or strategy == "ignore":
            plan.append({"index": index, "intent": intent, "strategy": strategy,
                         "value": None, "action": "skip"})
            continue

        action = _STRATEGY_ACTION.get(strategy)
        value = _value_for(intent, answers)
        # Unknown strategy shape, or no receipt for a value -> human, never fabricate.
        if action is None or value is None:
            plan.append({"index": index, "intent": intent, "strategy": strategy,
                         "value": None, "action": "needs_human"})
            continue

        plan.append({"index": index, "intent": intent, "strategy": strategy,
                     "value": value, "action": action})
    return plan


# ponytail: NEEDS_HUMAN is imported (not redefined) so the string stays identical to
# the guardrail's; re-export it here as the planner's sentinel for callers.
__all__ = ["plan_actions", "NEEDS_HUMAN"]
