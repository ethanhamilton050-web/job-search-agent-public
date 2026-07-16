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


def test_sponsorship_for_work_authorization_resolves_no_not_yes():
    """Regression (found live 2026-07-10 wiring the guardrail into the PNC filler):
    PNC asks 'Will you now or in the future require employer sponsorship for work
    authorization?'. That contains 'work authorization', so with work_authorized checked
    first it wrongly resolved 'Yes' (I'm authorized) -- a false claim that Ethan needs
    visa sponsorship, both untrue and self-sabotaging. A both-terms question is a
    SPONSORSHIP question; needs_sponsorship is now tried first, so it resolves 'No'."""
    q = ("Will you now or in the future require employer sponsorship for work "
         "authorization?")
    assert guardrail.resolve(q, FULL) == "No"
    # the pure work-authorization questions are unaffected by the reorder
    assert guardrail.resolve(Q_AUTH, FULL) == "Yes"
    assert guardrail.resolve(Q_VERIFY, FULL) == "Yes"


def test_work_authorization_status_questions_park_not_auto_yes():
    """Regression (found live 2026-07-10 wiring the guardrail into the PNC filler): the
    noun phrase 'work authorization' appears in STATUS questions that being work-authorized
    does NOT answer -- PNC's 'Is your current work authorization through STEM OPT or will
    you need employer paperwork?'. The old pattern's 'work.{0,25}authoriz' alt matched the
    noun phrase and auto-answered 'Yes', a false claim. Only the verb question ('authorized
    to work') resolves now; status questions route to the human."""
    stem = ("Is your current work authorization through STEM OPT or will you need "
            "employer paperwork?")
    assert guardrail.resolve(stem, FULL) == NEEDS_HUMAN
    # the genuine 'are you authorized to work' questions still resolve
    assert guardrail.resolve(Q_AUTH, FULL) == "Yes"
    assert guardrail.resolve(Q_VERIFY, FULL) == "Yes"


def test_reworded_workauth_and_sponsorship_resolve_from_the_same_facts():
    """Found live on SoFi 2026-07-14: employers reword the SAME universal questions.
    'eligible to work legally in the US' is a work-auth question (Yes); 'commence
    (sponsor) an immigration case in order to employ you' is a sponsorship question (No).
    Both must resolve from the facts already on file instead of parking for the human."""
    assert guardrail.resolve(
        "Are you currently eligible to work legally in the United States of America?",
        FULL) == "Yes"
    assert guardrail.resolve(
        "Are you eligible to work in the United States?", FULL) == "Yes"
    assert guardrail.resolve(
        "Are you legally authorized to work in the U.S.?", FULL) == "Yes"
    assert guardrail.resolve(
        "Will you now or in the future require SoFi to commence (“sponsor”) "
        "an immigration case in order to employ you?", FULL) == "No"


def test_reworded_workauth_does_not_fire_on_availability_or_status_questions():
    """The widened work-auth patterns must stay anchored to a legality/country context so
    they NEVER auto-answer 'Yes' to an availability question (a shift/overtime preference)
    or a visa-STATUS question that being authorized can't answer -- the exact over-fire
    class the earlier audits fixed. Every one of these must route to the human."""
    for q in (
        "Are you eligible to work weekends and holidays?",
        "Are you eligible to work overtime when required?",
        "Are you willing and able to work night shifts?",
        "Is your current work authorization through STEM OPT?",
        "When does your current work authorization expire?",
    ):
        assert guardrail.resolve(q, FULL) == NEEDS_HUMAN, q
    # ...and the real work-auth/sponsorship questions must STILL resolve
    assert guardrail.resolve(Q_AUTH, FULL) == "Yes"
    assert guardrail.resolve(Q_SPONSOR, FULL) == "No"


def test_employer_specific_questions_route_to_human():
    # Not in the universal table -> never auto-answered, whatever the profile holds.
    for q in (Q_RELATIVES, Q_KPMG, Q_SGO):
        assert guardrail.resolve(q, FULL) == NEEDS_HUMAN


def test_layer1_does_not_auto_answer_topically_unrelated_questions():
    """Regression (found live, 2026-07-09, by an overnight adversarial audit) --
    Layer 1 is the MORE dangerous gap than the Layer 2 one fixed alongside it,
    since resolve() has NO receipt check at all once a field is non-empty: the
    old unanchored patterns (bare "sponsor", bare "\\b18\\b", bare "military"/
    "reserves", an unbounded "authoriz.*work") fired on real but topically
    UNRELATED questions and auto-answered them with zero human review. Every
    one of these used to silently resolve to a confident Yes/No."""
    assert guardrail.resolve(
        "Do you authorize Citi to conduct a background check of your prior "
        "work history and criminal record?", FULL) == NEEDS_HUMAN
    assert guardrail.resolve(
        "Will you be available to start work on 18 June if an offer is extended?",
        FULL) == NEEDS_HUMAN
    assert guardrail.resolve(
        "Have you worked here for at least 18 months in a similar role?", FULL) == NEEDS_HUMAN
    assert guardrail.resolve(
        "Are you a sponsor or organizer of any outside charitable events using "
        "company resources?", FULL) == NEEDS_HUMAN
    assert guardrail.resolve(
        "Have you ever been convicted of a felony or misdemeanor involving "
        "military-grade weapons or explosives?", FULL) == NEEDS_HUMAN
    assert guardrail.resolve(
        "Do you currently maintain any brokerage, deposit, or reserves accounts "
        "with Citi or its affiliates that would need to be disclosed?", FULL) == NEEDS_HUMAN
    # and the real universal questions must still resolve correctly (not overcorrected)
    assert guardrail.resolve(Q_AUTH, FULL) == "Yes"
    assert guardrail.resolve(Q_VERIFY, FULL) == "Yes"
    assert guardrail.resolve(Q_SPONSOR, FULL) == "No"
    assert guardrail.resolve(Q_18, FULL) == "Yes"
    assert guardrail.resolve(Q_VET, FULL) == "No"


def test_age_and_veteran_rules_do_not_hit_lookalike_questions():
    """Regression (found live 2026-07-12): the age fact answered EXPERIENCE questions
    and the veteran fact answered supplier/family questions — false answers on a real
    application with zero review. Each of these must route to the human."""
    for q in (
        "Do you have at least 18 years of experience?",
        "Do you have 18 years experience in equity research?",
        "Is your company a veteran-owned business?",
        "Are you a veteran owned business?",
        "Are you a member of the armed forces community as a military spouse?",
    ):
        assert guardrail.resolve(q, FULL) == NEEDS_HUMAN, q
    # ...while the real age/veteran self-ID questions must STILL resolve
    assert guardrail.resolve(Q_18, FULL) == "Yes"
    assert guardrail.resolve("Are you 18 years of age or older?", FULL) == "Yes"
    assert guardrail.resolve(Q_VET, FULL) == "No"
    assert guardrail.resolve("Are you a protected veteran?", FULL) == "No"
    assert guardrail.resolve("Have you completed active duty military service?", FULL) == "No"


def test_missing_field_forces_human_even_for_known_question():
    incomplete = {"work_authorized": True, "needs_sponsorship": False}  # no veteran/18
    assert guardrail.resolve(Q_VET, incomplete) == NEEDS_HUMAN
    assert guardrail.resolve(Q_18, incomplete) == NEEDS_HUMAN
    assert guardrail.resolve(Q_VET, {**FULL, "is_veteran": ""}) == NEEDS_HUMAN


def test_layer2_keeps_answer_only_with_a_real_receipt():
    assert guardrail.verify_ai_answer(Q_VET, "No", "is_veteran", FULL) == "No"
    # Invented citation -> overridden to human, however confident the AI was.
    assert guardrail.verify_ai_answer(Q_VET, "No", "served_in_navy", FULL) == NEEDS_HUMAN
    # Cited a real field that is empty -> still human.
    assert guardrail.verify_ai_answer(Q_VET, "No", "is_veteran", {"is_veteran": ""}) == NEEDS_HUMAN
    # AI flagged it itself -> stays flagged.
    assert guardrail.verify_ai_answer(Q_VET, NEEDS_HUMAN, "none", FULL) == NEEDS_HUMAN


def test_layer2_rejects_answer_that_contradicts_the_cited_field():
    """Regression: a fabricating AI must not get a confident Yes/No past Layer 2 just
    by citing a real, populated field whose VALUE says the opposite. is_veteran is
    False, so a cited "Yes" is a fabrication and must route to the human; only the
    answer the field actually proves ("No") may stand."""
    assert guardrail.verify_ai_answer(Q_VET, "Yes", "is_veteran", FULL) == NEEDS_HUMAN
    assert guardrail.verify_ai_answer(Q_VET, "No", "is_veteran", FULL) == "No"
    # needs_sponsorship is False -> a cited "Yes" contradicts it and is rejected.
    assert guardrail.verify_ai_answer(Q_SPONSOR, "Yes", "needs_sponsorship", FULL) == NEEDS_HUMAN


def test_layer2_rejects_a_real_receipt_that_is_topically_irrelevant():
    """Regression: found live by an overnight adversarial audit (2026-07-09). The old
    code only checked that the cited field was real and its VALUE matched -- never
    that the field had anything to do with the question. A fabricating (or merely
    confused) AI could cite is_veteran=False to answer a COMPLETELY UNRELATED felony/
    background-check question and it would sail through as a "verified" receipt. That
    must now be rejected even though the field is real and the value technically
    agrees with the claimed answer."""
    felony_q = "Have you ever been convicted of a felony or misdemeanor?"
    assert guardrail.verify_ai_answer(felony_q, "No", "is_veteran", FULL) == NEEDS_HUMAN
    # Same field, wrong question, even for a topic-adjacent-sounding one.
    assert guardrail.verify_ai_answer(Q_SPONSOR, "No", "is_veteran", FULL) == NEEDS_HUMAN
    # An employer-specific field (real in workday_answers.json, e.g. veteran_status)
    # is never confirmable here at all -- it has no _RULES entry, so no question can
    # ever pass the relevance check for it. That's intentional: see verify_ai_answer's
    # docstring for why "always ask the human" is the safe default until broader
    # relevance-mapping is designed.
    assert guardrail.verify_ai_answer(Q_VET, "No", "veteran_status", {**FULL, "veteran_status": "No"}) == NEEDS_HUMAN


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
