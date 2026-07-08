"""The honesty guardrail is the product's core promise, so it gets real tests.

These mirror the feasibility experiment (experiments/field_map_test.py) as a
regression suite: the same real Citi screening questions, proving that universal
questions resolve from the profile while employer-specific and unbacked ones are
always routed to the human — never guessed.
"""
from jobagent import guardrail
from jobagent.guardrail import NEEDS_HUMAN

# Real Citi screening questions (text as shown on the application).
Q_AUTH = "Are you legally authorized to work in the country or jurisdiction where the position to which you are applying is located?"
Q_VERIFY = "Can you, within the time period prescribed by law, submit verification of both your identity and authorization to work in the country or jurisdiction where the position is located?"
Q_SPONSOR = "Will you, now or in the future, require sponsorship for employment in the country or jurisdiction where the position to which you are applying is located?"
Q_18 = "Are you at least 18 years of age?"
Q_VET = "Are you serving, or have you ever served in the Armed Forces of the United States of America (to include active duty, Reserves, or National Guard)?"
Q_RELATIVES = "Do you have any relatives, or persons in any other Covered Relationships, currently working for Citi, or who are part of Citi's Senior Management?"
Q_KPMG = "Were you a partner and/or have you ever been employed by KPMG LLP and/or its members and affiliates worldwide in the last three (3) years?"
Q_SGO = "Are you a referral or relative of a current Senior Government Official (SGO)?"

FULL = {
    "work_authorized": True,
    "needs_sponsorship": False,
    "is_over_18": True,
    "is_veteran": False,
}


def test_universal_questions_resolve_from_profile():
    assert guardrail.resolve(Q_AUTH, FULL) == "Yes"
    assert guardrail.resolve(Q_VERIFY, FULL) == "Yes"
    assert guardrail.resolve(Q_SPONSOR, FULL) == "No"
    assert guardrail.resolve(Q_18, FULL) == "Yes"
    assert guardrail.resolve(Q_VET, FULL) == "No"


def test_employer_specific_questions_route_to_human():
    # Not in the universal table -> never auto-answered, whatever the profile holds.
    for q in (Q_RELATIVES, Q_KPMG, Q_SGO):
        assert guardrail.resolve(q, FULL) == NEEDS_HUMAN


def test_missing_field_forces_human_even_for_known_question():
    incomplete = {"work_authorized": True, "needs_sponsorship": False}  # no veteran/18
    assert guardrail.resolve(Q_VET, incomplete) == NEEDS_HUMAN
    assert guardrail.resolve(Q_18, incomplete) == NEEDS_HUMAN
    assert guardrail.resolve(Q_VET, {**FULL, "is_veteran": ""}) == NEEDS_HUMAN


def test_layer2_keeps_answer_only_with_a_real_receipt():
    assert guardrail.verify_ai_answer("No", "is_veteran", FULL) == "No"
    # Invented citation -> overridden to human, however confident the AI was.
    assert guardrail.verify_ai_answer("No", "served_in_navy", FULL) == NEEDS_HUMAN
    # Cited a real field that is empty -> still human.
    assert guardrail.verify_ai_answer("No", "is_veteran", {"is_veteran": ""}) == NEEDS_HUMAN
    # AI flagged it itself -> stays flagged.
    assert guardrail.verify_ai_answer(NEEDS_HUMAN, "none", FULL) == NEEDS_HUMAN


def test_layer2_rejects_answer_that_contradicts_the_cited_field():
    """Regression: a fabricating AI must not get a confident Yes/No past Layer 2 just
    by citing a real, populated field whose VALUE says the opposite. is_veteran is
    False, so a cited "Yes" is a fabrication and must route to the human; only the
    answer the field actually proves ("No") may stand."""
    assert guardrail.verify_ai_answer("Yes", "is_veteran", FULL) == NEEDS_HUMAN
    assert guardrail.verify_ai_answer("No", "is_veteran", FULL) == "No"
    # needs_sponsorship is False -> a cited "Yes" contradicts it and is rejected.
    assert guardrail.verify_ai_answer("Yes", "needs_sponsorship", FULL) == NEEDS_HUMAN


def test_resolve_does_not_flip_a_stored_string_no_into_yes():
    """Regression: an answer bank storing human strings must not have "No" coerced to
    a truthy "Yes". bool("No") is True, so the old code flipped it; a stored false
    token now correctly resolves to "No", and an unreadable value routes to human."""
    assert guardrail.resolve(Q_18, {"is_over_18": "No"}) == "No"
    assert guardrail.resolve(Q_18, {"is_over_18": "false"}) == "No"
    assert guardrail.resolve(Q_18, {"is_over_18": "0"}) == "No"
    assert guardrail.resolve(Q_18, {"is_over_18": "Yes"}) == "Yes"
    # Ambiguous, non-token value -> never guessed.
    assert guardrail.resolve(Q_18, {"is_over_18": "maybe"}) == NEEDS_HUMAN
