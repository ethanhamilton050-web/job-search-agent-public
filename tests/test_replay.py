"""Deterministic replay planner: the real Citi field-map turns into the right per-field
plan, missing answers route to a human, page chrome is skipped, and nothing is ever
fabricated. Pure logic — no AI, no browser, no network.

Ground truth is the real Citi "My Information" FIXTURE from the feasibility
experiment (experiments/field_map_test.py): the correct (intent, strategy) per field.
"""
import sys
from pathlib import Path

# The FIXTURE lives in experiments/, which isn't an importable package; add its dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
from field_map_test import FIXTURE  # noqa: E402

from jobagent.workday import replay  # noqa: E402
from jobagent.workday.replay import NEEDS_HUMAN  # noqa: E402

# The Citi correct map, in fieldmap output shape, straight from the FIXTURE ground truth.
DESCRIPTORS = [raw for raw, _, _ in FIXTURE]
MAPPING = [{"i": i, "intent": intent, "strategy": strat}
           for i, (_, intent, strat) in enumerate(FIXTURE)]

# A sample answer bank with a value for every intent the Citi page asks for.
ANSWERS = {
    "first_name": "Ethan",
    "last_name": "Hamilton",
    "preferred_name": "Ethan",
    "address_line1": "123 Main St",
    "city": "Jersey City",
    "state": "New Jersey",
    "postal_code": "07302",
    "country": "United States of America",
    "phone": "5551234567",
    "phone_type": "Mobile",
    "phone_country_code": "+1",
    "phone_extension": "100",
    "previous_worker": "No",
    "how_did_you_hear": "Company website",
}

_ACTION_FOR_STRATEGY = {"text": "fill", "select": "select", "radio": "radio",
                        "checkbox": "checkbox", "file": "fill"}


def _by_index(plan):
    return {row["index"]: row for row in plan}


def test_correct_map_plus_answers_yields_right_plan():
    plan = replay.plan_actions(DESCRIPTORS, MAPPING, ANSWERS)
    assert len(plan) == len(FIXTURE)
    rows = _by_index(plan)
    for i, (_, intent, strat) in enumerate(FIXTURE):
        row = rows[i]
        assert row["intent"] == intent
        assert row["strategy"] == strat
        if intent == "ignore":
            assert row["action"] == "skip" and row["value"] is None
        else:
            # every real field has an answer here, so it gets its concrete action + value
            assert row["action"] == _ACTION_FOR_STRATEGY[strat]
            assert row["value"] == ANSWERS[intent]


def test_ignore_chrome_is_skipped_not_filled():
    plan = _by_index(replay.plan_actions(DESCRIPTORS, MAPPING, ANSWERS))
    # Fields 0 and 1 are the language/settings menu buttons (intent ignore).
    for i in (0, 1):
        assert plan[i]["action"] == "skip"
        assert plan[i]["value"] is None


def test_missing_answer_routes_to_human_never_guessed():
    # Drop first_name and blank out phone: both must become needs_human, value None.
    answers = dict(ANSWERS)
    del answers["first_name"]
    answers["phone"] = ""
    plan = _by_index(replay.plan_actions(DESCRIPTORS, MAPPING, answers))
    first_i = next(i for i, (_, it, _) in enumerate(FIXTURE) if it == "first_name")
    phone_i = next(i for i, (_, it, _) in enumerate(FIXTURE) if it == "phone")
    for i in (first_i, phone_i):
        assert plan[i]["action"] == "needs_human"
        assert plan[i]["value"] is None
    # A field that still has its answer is unaffected.
    last_i = next(i for i, (_, it, _) in enumerate(FIXTURE) if it == "last_name")
    assert plan[last_i]["action"] == "fill" and plan[last_i]["value"] == "Hamilton"


def test_empty_answer_bank_fabricates_nothing():
    # No answers at all: every non-chrome field flags for a human, none gets a value.
    plan = replay.plan_actions(DESCRIPTORS, MAPPING, {})
    for row in plan:
        if row["intent"] == "ignore":
            assert row["action"] == "skip"
        else:
            assert row["action"] == "needs_human"
        assert row["value"] is None  # nothing is ever invented


def test_empty_list_or_none_answer_is_treated_as_missing():
    answers = {"first_name": [], "last_name": None, "city": "Jersey City"}
    plan = _by_index(replay.plan_actions(
        DESCRIPTORS,
        [{"i": 0, "intent": "first_name", "strategy": "text"},
         {"i": 1, "intent": "last_name", "strategy": "text"},
         {"i": 2, "intent": "city", "strategy": "text"}],
        answers))
    assert plan[0]["action"] == "needs_human" and plan[0]["value"] is None
    assert plan[1]["action"] == "needs_human" and plan[1]["value"] is None
    assert plan[2]["action"] == "fill" and plan[2]["value"] == "Jersey City"


def test_unknown_strategy_routes_to_human():
    # A strategy the executor doesn't understand must not be guessed into an action.
    plan = replay.plan_actions(
        DESCRIPTORS,
        [{"i": 0, "intent": "first_name", "strategy": "spaceship"}],
        ANSWERS)
    assert plan[0]["action"] == "needs_human" and plan[0]["value"] is None


def test_file_strategy_maps_to_fill():
    plan = replay.plan_actions(
        DESCRIPTORS,
        [{"i": 0, "intent": "resume_file", "strategy": "file"}],
        {"resume_file": "C:/input/resume.pdf"})
    assert plan[0]["action"] == "fill" and plan[0]["value"] == "C:/input/resume.pdf"


def test_plan_preserves_mapping_order():
    plan = replay.plan_actions(DESCRIPTORS, MAPPING, ANSWERS)
    assert [row["index"] for row in plan] == list(range(len(FIXTURE)))
