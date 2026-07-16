"""The production honesty pipeline is the product's promise, so it gets an
adversarial test, not just a happy path.

Reuses the REAL Citi questions from experiments/field_map_test.py: the universal
ones (work auth, sponsorship, 18, veteran) must resolve at Layer 1 from a full
answer bank, and the employer-specific ones (relatives at Citi, ex-KPMG, SGO/SCP)
must survive a FABRICATING AI — a fake answerer that returns confident Yes/No with
invented or empty citations — by being routed to the human. Zero fabrications may
reach the output.
"""
from experiments.field_map_test import SCREENING
from jobagent import screening
from jobagent.guardrail import NEEDS_HUMAN

# Real Citi questions, indexed exactly as the experiment fixture orders them.
Q = [q for q, _ in SCREENING]
Q_AUTH, Q_VERIFY, Q_RELATIVES, Q_KPMG, Q_SGO, Q_SCP, Q_SPONSOR, Q_18, Q_VET = Q

FULL = {
    "work_authorized": True,
    "needs_sponsorship": False,
    "is_over_18": True,
    "is_veteran": False,
}

UNIVERSAL = [Q_AUTH, Q_VERIFY, Q_SPONSOR, Q_18, Q_VET]
EMPLOYER_SPECIFIC = [Q_RELATIVES, Q_KPMG, Q_SGO, Q_SCP]


def _fabricator(question):
    """An AI that LIES: confident Yes/No with a fake or empty citation for every
    employer-specific question. If any of these reach the output, the promise broke.
    """
    fakes = {
        Q_RELATIVES: ("No", "no_citi_relatives"),   # field not in answer bank
        Q_KPMG: ("No", ""),                          # empty citation
        Q_SGO: ("No", "not_an_sgo"),                 # invented field
        Q_SCP: ("No", "definitely_clean"),           # invented field
    }
    return fakes.get(question, ("Yes", "totally_real_field"))


def test_universal_questions_resolve_at_layer1():
    results = screening.answer_questions(UNIVERSAL, FULL)
    by_q = {r["question"]: r for r in results}
    assert by_q[Q_AUTH]["answer"] == "Yes"
    assert by_q[Q_AUTH]["layer"] == "profile"
    assert by_q[Q_AUTH]["source"] == "work_authorized"
    assert by_q[Q_SPONSOR]["answer"] == "No"
    assert by_q[Q_SPONSOR]["source"] == "needs_sponsorship"
    assert by_q[Q_18]["answer"] == "Yes" and by_q[Q_18]["source"] == "is_over_18"
    assert by_q[Q_VET]["answer"] == "No" and by_q[Q_VET]["source"] == "is_veteran"
    assert by_q[Q_VERIFY]["answer"] == "Yes"  # verification-of-work-auth rule
    # A full answer bank leaves nothing for the human on universal questions.
    assert screening.summarize(results) == {"answered": 5, "needs_human": 0}


def test_fabricating_ai_cannot_sneak_answers_past_layer2():
    """ADVERSARIAL: the fake AI is confident on every employer-specific question,
    but each citation is fake/empty, so all four MUST come back NEEDS_HUMAN."""
    results = screening.answer_questions(EMPLOYER_SPECIFIC, FULL, ai_answer_fn=_fabricator)
    for r in results:
        assert r["answer"] == NEEDS_HUMAN, f"fabrication leaked: {r}"
        assert r["layer"] == "human"
        assert r["source"] == ""
    assert screening.summarize(results) == {"answered": 0, "needs_human": 4}


def test_no_fabrication_reaches_output_on_full_citi_set():
    """The whole real Citi page: 5 universal answered from profile, 4 employer-
    specific flagged for the human despite a lying AI. No Yes/No lacks a receipt."""
    results = screening.answer_questions(Q, FULL, ai_answer_fn=_fabricator)
    assert screening.summarize(results) == {"answered": 5, "needs_human": 4}
    for r in results:
        if r["answer"] != NEEDS_HUMAN:
            # Every surviving Yes/No must carry a source that is real + non-empty.
            assert r["layer"] == "profile"
            assert r["source"] and FULL.get(r["source"]) not in (None, "")


def test_ai_citing_a_real_but_irrelevant_field_is_flagged_not_surfaced():
    """Regression (found live, 2026-07-09, by an overnight adversarial audit): a
    non-universal question the profile can't answer, and the AI cites a real,
    non-empty field that has NOTHING to do with the question -- this must be
    flagged for the human, not surfaced as a verified "ai+receipt" answer. The old
    code only checked the field was real and non-empty; it never checked the field
    was actually relevant to what was asked, so this exact case used to wrongly
    pass as answer="Yes", layer="ai+receipt"."""
    q = "Do you consent to a background check?"
    ai = lambda _q: ("Yes", "work_authorized")  # noqa: E731 — real field, wrong topic
    (r,) = screening.answer_questions([q], FULL, ai_answer_fn=ai)
    assert r == {"question": q, "answer": NEEDS_HUMAN, "source": "", "layer": "human"}


def test_no_ai_means_unknown_questions_go_to_human():
    q = "Do you consent to a background check?"
    (r,) = screening.answer_questions([q], FULL)
    assert r == {"question": q, "answer": NEEDS_HUMAN, "source": "", "layer": "human"}


def test_ai_yes_citing_a_false_field_is_flagged_not_surfaced():
    """Regression via the public entry point: a fabricating AI answers a compliance
    question "Yes" and cites is_veteran, which is literally False. The value
    contradicts the answer, so it must come back NEEDS_HUMAN — never ai+receipt."""
    lying = lambda _q: ("Yes", "is_veteran")  # noqa: E731 — real field, wrong value
    (r,) = screening.answer_questions([Q_SCP], FULL, ai_answer_fn=lying)
    assert r["answer"] == NEEDS_HUMAN
    assert r["layer"] == "human" and r["source"] == ""


def test_stored_string_no_is_not_surfaced_as_a_proven_yes():
    """Regression via the public entry point: an answer bank holding "No" as a string
    must not surface a confident Layer-1 "Yes" that contradicts the stored value."""
    (r,) = screening.answer_questions([Q_18], {"is_over_18": "No"})
    assert r["answer"] == "No" and r["layer"] == "profile"


def test_missing_universal_field_falls_to_human_not_ai_guess():
    """A known question whose backing field is empty must NOT be handed to the AI to
    guess — resolve returns NEEDS_HUMAN and, with no AI, it routes to the human."""
    incomplete = {"work_authorized": True, "needs_sponsorship": False}  # no veteran
    (r,) = screening.answer_questions([Q_VET], incomplete)
    assert r["answer"] == NEEDS_HUMAN and r["layer"] == "human"
