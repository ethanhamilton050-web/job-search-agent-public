"""Grounded-fact resolvers (Buckets A-values / B / C) — the honesty core extended.

Same contract as the Yes/No guardrail: answer ONLY from a fact the human supplied once,
otherwise NEEDS_HUMAN (park). These cover the classes Ethan flagged reviewing 33 real
parked questions on 2026-07-14: values he already gave (education, home country), "ever
worked at X" from a COMPLETE job history, "currently a <company> employee", and
conflict-of-interest related-party questions. The negative cases are the point: an
incomplete list, or a fact left blank, must NEVER produce a confident answer.
"""
from jobagent import guardrail
from jobagent.guardrail import NEEDS_HUMAN

HIST = {"employers_all": ["Goldman Sachs", "Morgan Stanley", "JPMorgan Chase"],
        "employment_history_complete": True}
NO_PARTIES = {"related_party_complete": True}   # all lists empty -> nothing to disclose


# ---- Bucket A: grounded VALUE facts -------------------------------------------------

def test_value_answer_fills_education_and_home_country():
    assert guardrail.value_answer(
        "What is the highest level of education you have completed?",
        {"highest_education": "Bachelor's Degree"}) == "Bachelor's Degree"
    assert guardrail.value_answer("Highest degree earned", {"highest_education": "MBA"}) == "MBA"
    # home country reads home_country, else falls back to the existing country field
    assert guardrail.value_answer("Home Address Country",
                                  {"country": "United States of America"}) == "United States of America"
    assert guardrail.value_answer("Country of residence", {"home_country": "USA"}) == "USA"


def test_value_answer_parks_when_recognized_but_no_fact_on_file():
    assert guardrail.value_answer("Highest level of education?", {}) == NEEDS_HUMAN


def test_state_of_residence_fills_from_address_on_file():
    assert guardrail.value_answer("Which U.S. State or Canadian Province do you reside in?",
                                  {"state": "New Jersey"}) == "New Jersey"
    assert guardrail.value_answer("What state do you currently reside in?", {"state": "NJ"}) == "NJ"
    # "United States" must not be read as a state question
    assert guardrail.value_answer("Are you authorized to work in the United States?",
                                  {"state": "NJ"}) == NEEDS_HUMAN


def test_disclosures_answer_no_when_nothing_to_disclose():
    clean = {"related_party_complete": True}          # no disclosures, no conflict lists
    # FINRA licenses are NOT handled here anymore (finra_answer owns them) -> defers
    assert guardrail.disclosure_answer("Do you hold, or intend to hold, any FINRA licenses?",
                                       clean) == NEEDS_HUMAN
    compound = ("Do you have: a) Personal/Familial Relationships with employees; b) Outside "
                "Business Activities; c) investment greater than 5% of a publicly-traded company; "
                "d) investment in a private competitor; or e) Intellectual Property to retain?")
    assert guardrail.disclosure_answer(compound, clean) == "No"


def test_finra_answer_yes_only_for_finra_credentials_never_false_no():
    held = {"finra_licenses": ["Series 6", "Series 63", "SIE"]}
    assert guardrail.finra_answer(
        "Do you currently hold, or intend to hold, any FINRA licenses if employed by SoFi?",
        held) == "Yes"
    # a non-FINRA license alone (state insurance producer) must NOT answer a FINRA question Yes
    ins = {"finra_licenses": ["Health and Life Insurance Producer"]}
    assert guardrail.finra_answer("Do you hold any FINRA licenses?", ins) == NEEDS_HUMAN
    # NO licenses on file -> PARK, never a false 'No' (denying a held license is a real problem)
    assert guardrail.finra_answer("Do you hold any FINRA licenses?", {}) == NEEDS_HUMAN
    # the "WHICH license(s)?" list is for the checkbox filler, not a Yes/No
    assert guardrail.finra_answer("What FINRA license(s), if any, do you currently hold?", held) == NEEDS_HUMAN
    assert guardrail.finra_answer("Are you at least 18 years of age?", held) == NEEDS_HUMAN


def test_disclosures_park_when_something_is_on_file_or_not_affirmed():
    has = {"related_party_complete": True, "professional_disclosures": ["Series 7 license"]}
    assert guardrail.disclosure_answer("Do you hold any FINRA licenses?", has) == NEEDS_HUMAN
    assert guardrail.disclosure_answer("Do you hold any FINRA licenses?", {}) == NEEDS_HUMAN  # not affirmed
    assert guardrail.disclosure_answer("Are you at least 18 years of age?",
                                       {"related_party_complete": True}) == NEEDS_HUMAN  # unrelated


def test_value_answer_does_not_mistake_work_auth_country_for_home_country():
    # "authorized to work in the country" is a work-auth question, not a residence question.
    assert guardrail.value_answer(
        "Are you authorized to work in the country where the role is located?",
        {"country": "United States of America"}) == NEEDS_HUMAN


# ---- Bucket B: "ever worked at X" from a complete history ---------------------------

def test_ever_worked_yes_when_a_listed_employer_is_named():
    assert guardrail.ever_worked_answer("Have you ever been employed by Goldman Sachs?", HIST) == "Yes"
    # legal-suffix noise doesn't break the match
    assert guardrail.ever_worked_answer("Have you ever worked at Goldman Sachs & Co. LLC?", HIST) == "Yes"


def test_ever_worked_no_only_when_history_is_marked_complete():
    sofi_q = ("Have you worked at or been a consultant for SoFi or any company subsequently "
              "acquired by a SoFi entity (including Galileo Financial Technologies, Technisys)?")
    assert guardrail.ever_worked_answer(sofi_q, HIST) == "No"          # complete list, not listed
    deloitte_q = "Are you currently employed with or have been employed by Deloitte?"
    assert guardrail.ever_worked_answer(deloitte_q, HIST) == "No"
    # ...but an INCOMPLETE list can never prove a 'No' -> park
    incomplete = {"employers_all": ["Goldman Sachs"]}   # no completeness affirmation
    assert guardrail.ever_worked_answer(deloitte_q, incomplete) == NEEDS_HUMAN


def test_ever_worked_ignores_willingness_and_unrelated_questions():
    # "willing to work at our NYC office" is relocation, NOT employment history.
    assert guardrail.ever_worked_answer("Are you willing to work at our New York office?", HIST) == NEEDS_HUMAN
    assert guardrail.ever_worked_answer("Are you at least 18 years of age?", HIST) == NEEDS_HUMAN


def test_conditional_if_employed_clause_does_not_answer_a_licensing_question():
    """Self-check caught this 2026-07-14: 'Do you currently hold ... any FINRA licenses if
    employed by SoFi?' is about LICENSES; the subordinate 'if employed by SoFi' must NOT make
    the employment resolvers answer 'No'. Both must park it for the human."""
    q = "Do you currently hold, or intend to hold, any FINRA licenses if employed by SoFi?"
    assert guardrail.ever_worked_answer(q, HIST) == NEEDS_HUMAN
    assert guardrail.current_employer_answer(q, HIST) == NEEDS_HUMAN


# ---- "Currently a <company> employee?" (safe 'No' only) ----------------------------

def test_current_employer_answers_no_when_not_in_history():
    assert guardrail.current_employer_answer(
        "Are you currently a SoFi, Galileo or Technisys employee?", HIST) == "No"


def test_current_employer_never_auto_yes_and_excludes_work_auth():
    # named in history -> ambiguous (past vs present) -> park, never auto-'Yes'
    assert guardrail.current_employer_answer("Are you currently a Goldman Sachs employee?", HIST) == NEEDS_HUMAN
    # a work-authorization phrasing must not be captured here
    assert guardrail.current_employer_answer(
        "Are you currently authorized to work in the US?", HIST) == NEEDS_HUMAN
    # without an affirmed-complete history, even the 'No' case parks
    assert guardrail.current_employer_answer(
        "Are you currently a SoFi employee?", {"employers_all": ["Goldman Sachs"]}) == NEEDS_HUMAN


# ---- Bucket C: related-party (conflict of interest) --------------------------------

def test_related_party_no_when_lists_empty_and_affirmed_complete():
    assert guardrail.related_party_answer(
        "Do you have any relatives or family members currently working for or who are "
        "insiders of the company?", NO_PARTIES) == "No"
    assert guardrail.related_party_answer(
        "Are you an officer, director, or 10% owner of a publicly traded company?",
        NO_PARTIES) == "No"
    assert guardrail.related_party_answer(
        "Are you or an immediate family member a senior government official?", NO_PARTIES) == "No"


def test_related_party_parks_on_any_potential_match():
    has_family = {"related_party_complete": True,
                  "insiders": [{"who": "mother", "company": "XYZ Corp", "role": "Director"}]}
    assert guardrail.related_party_answer(
        "Do you have relatives who are employees of the company?", has_family) == NEEDS_HUMAN
    is_self = {"related_party_complete": True,
               "insiders": [{"who": "self", "company": "ACME", "role": "Director", "amount": "12%"}]}
    assert guardrail.related_party_answer(
        "Are you an officer or director of a public company?", is_self) == NEEDS_HUMAN


def test_related_party_recognizes_many_insider_and_official_wordings():
    """Ethan's ask: catch the SAME question in its many forms. All of these are the standard
    insider / official conflict-of-interest disclosures and, with nothing to disclose and the
    list affirmed complete, must resolve 'No' -- not park."""
    for q in (
        "Is any immediate family member a director or executive officer of a publicly traded company?",
        "Are you a control person or 10% beneficial owner of any SEC-reporting issuer?",
        "Are you an insider of a publicly listed company?",
        "Do you or a household member currently hold public office or serve as an elected official?",
        "Are you a politically exposed person (PEP)?",
        "Does a covered relationship include anyone who is a restricted person of a public company?",
    ):
        assert guardrail.related_party_answer(q, NO_PARTIES) == "No", q


def test_non_compete_agreement_question_parks_not_no():
    """Reviewing real parked questions 2026-07-14: 'subject to any agreement with a former
    employer (non-solicitation or non-compete)' was wrongly answered 'No' off 'former
    employer'. It's about a CONTRACT only Ethan knows -> must park, not auto-answer."""
    q = ("Are you currently subject to any agreement with a former employer or third party "
         "(such as a non-solicitation or non-compete agreement) that may limit your duties?")
    assert guardrail.ever_worked_answer(q, HIST) == NEEDS_HUMAN
    assert guardrail.current_employer_answer(q, HIST) == NEEDS_HUMAN


def test_insider_threshold_matches_any_percentage():
    """The ownership threshold varies by employer (5% SEC significant-holder, 10% Section 16),
    so recognition must not hinge on a specific number."""
    for q in (
        "Do you own more than 5% of the outstanding shares of a publicly-traded company?",
        "Are you a 10% beneficial owner of any publicly traded company?",
        "Do you hold a 5 percent or greater stake in a public company?",
    ):
        assert guardrail.related_party_answer(q, NO_PARTIES) == "No", q


def test_relatives_working_at_the_hiring_company_use_their_own_list():
    """'Relatives working at the company' is broader than public-company insiders, so it has
    its own list. No only when that list (and the insider list) is empty and affirmed."""
    q = "Do you have any relatives currently working for our company or its affiliates?"
    assert guardrail.related_party_answer(q, NO_PARTIES) == "No"          # none declared
    has_rel = {"related_party_complete": True,
               "employer_relatives": [{"who": "cousin", "company": "Citi"}]}
    assert guardrail.related_party_answer(q, has_rel) == NEEDS_HUMAN      # a relative at an employer -> park


def test_relatives_working_at_a_company_is_not_the_applicants_own_employer():
    """Found 2026-07-14: Citi's 'relatives currently working for Citi' was resolving via the
    current-employer rule (about the APPLICANT) for the wrong reason. That rule must defer on
    any family question; related_party answers it from the affirmed lists instead."""
    q = "Do you have any relatives currently working for Citi or part of Citi's senior management?"
    assert guardrail.current_employer_answer(q, HIST) == NEEDS_HUMAN     # not about YOU
    assert guardrail.related_party_answer(q, NO_PARTIES) == "No"          # answered honestly here


def test_compound_conflict_question_with_uncovered_dimensions_parks():
    """A multi-part conflict question that also asks about outside business activities, IP,
    or private-company investments must PARK -- a blanket 'No' would deny those too, and the
    affirmation doesn't cover them. (Robinhood's a-e question, reviewed 2026-07-14.)"""
    q = ("Do you have: a) any Personal/Familial Relationships with current employees; b) any "
         "Outside Business Activities you wish to continue; c) any investment greater than 5% "
         "of a publicly-traded company; d) any investment in a private competitor; or e) any "
         "Intellectual Property you wish to retain?")
    assert guardrail.related_party_answer(q, NO_PARTIES) == NEEDS_HUMAN


def test_related_party_parks_when_not_affirmed_complete():
    # no related_party_complete flag -> we can't prove a 'No', so park
    assert guardrail.related_party_answer(
        "Do you have relatives who work for the company?", {}) == NEEDS_HUMAN


def test_grounded_resolvers_never_touch_unrelated_questions():
    for q in ("Are you at least 18 years of age?",
              "Will you require sponsorship for employment?",
              "How did you hear about this role?"):
        assert guardrail.ever_worked_answer(q, HIST) == NEEDS_HUMAN, q
        assert guardrail.related_party_answer(q, NO_PARTIES) == NEEDS_HUMAN, q
        assert guardrail.value_answer(q, {"highest_education": "BA"}) == NEEDS_HUMAN, q
