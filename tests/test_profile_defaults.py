"""Deriving My Info starting values from the resume so facts aren't re-typed.

The one bit of logic worth guarding is picking the HIGHEST degree from the resume's
education line(s) — checked high to low, keyword-based, from the resume text only.
"""
from jobagent.workday import answer_bank


def test_highest_education_picks_the_highest_level():
    assert answer_bank._highest_education(["B.S. Finance, State U", "MBA, Wharton"]) == "MBA"
    assert answer_bank._highest_education(["Bachelor of Arts in Economics"]) == "Bachelor's Degree"
    assert answer_bank._highest_education(["Master of Science", "Bachelor of Science"]) == "Master's Degree"
    assert answer_bank._highest_education(["Ph.D. in Economics"]) == "Doctorate (PhD)"
    assert answer_bank._highest_education(["Associate of Applied Science"]) == "Associate's Degree"
    assert answer_bank._highest_education(["High School Diploma"]) == "High School Diploma"


def test_highest_education_blank_when_no_degree_keyword():
    assert answer_bank._highest_education([]) == ""
    assert answer_bank._highest_education(["Certificate in Data Analytics"]) == ""
    assert answer_bank._highest_education("") == ""


def test_licenses_pulled_from_resume_text():
    """Ethan's real credentials, and the shorthand list form ("Series 6 & 63")."""
    lics = answer_bank._licenses_from_text(
        "Licenses: Series 6 & 63, SIE. Health and Life Insurance Producer.")
    assert lics == ["Series 6", "Series 63", "SIE", "Health and Life Insurance Producer"]
    # comma/"and" separated list, deduped
    assert answer_bank._licenses_from_text("Series 7, 66 and Series 66") == ["Series 7", "Series 66"]


def test_in_progress_degree_is_never_claimed_as_completed():
    """Résumé-fraud guard: a degree you're still earning must not be asserted as held."""
    assert answer_bank._highest_education(["MBA Candidate, Wharton (expected 2027)"]) == ""
    assert answer_bank._highest_education(["Pursuing Bachelor of Science in Finance"]) == ""
    assert answer_bank._highest_education(["Ph.D. student, ABD"]) == ""
    assert answer_bank._highest_education(["Coursework toward Master of Science"]) == ""


def test_highest_COMPLETED_degree_wins_over_an_in_progress_higher_one():
    # holds a Bachelor's, still earning the MBA -> claim the Bachelor's, never the MBA
    assert answer_bank._highest_education(
        ["B.S. Finance, State University (2019)", "MBA Candidate, Wharton (expected 2027)"]
    ) == "Bachelor's Degree"


def test_licenses_dont_false_positive():
    assert answer_bank._licenses_from_text("Led Series A fundraising in 2020") == []      # "Series A", a year
    assert answer_bank._licenses_from_text("Administered employee health insurance plans") == []  # no producer/license anchor
    assert answer_bank._licenses_from_text("") == []
