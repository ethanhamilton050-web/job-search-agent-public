"""Offline checks for the filler's pure helpers (date/education parsing).

The browser-driving code is host-only (needs Playwright + real logins), so the
only thing worth a unit test here is the fragile text->fields parsing.
"""
import os

from jobagent.workday import filler


def test_parse_dates_month_year():
    (sm, sy), (em, ey) = filler._parse_dates("Jan 2025 – April 2026")
    assert (sm, sy) == (1, 2025)
    assert (em, ey) == (4, 2026)


def test_parse_dates_present_end():
    (sm, sy), (em, ey) = filler._parse_dates("Dec 2022 - Present")
    assert (sm, sy) == (12, 2022)
    assert (em, ey) == (None, None)  # current job -> tick "I currently work here"


def test_parse_dates_year_only():
    (sm, sy), (em, ey) = filler._parse_dates("2021 – 2023")
    assert sy == 2021 and ey == 2023


def test_education_guess_pulls_school_and_gpa():
    ans = {"education": [
        "Example University – Bristol, RI",
        "B.S. in Finance | Major GPA: 3.8 | May 2023",
    ]}
    edu = filler._education_guess(ans)[0]
    assert edu["school"].startswith("Example University")
    assert edu["gpa"] == "3.8"
    assert edu["end_year"] == "2023"


def test_pace_clamps_and_defaults():
    saved = os.environ.pop("JOBAGENT_SLOW", None)
    try:
        assert filler._pace() == 1.0          # unset -> normal speed
        os.environ["JOBAGENT_SLOW"] = "2"
        assert filler._pace() == 2.0
        os.environ["JOBAGENT_SLOW"] = "0.01"  # too fast -> clamped up
        assert filler._pace() == 0.5
        os.environ["JOBAGENT_SLOW"] = "99"    # too slow -> clamped down
        assert filler._pace() == 6.0
        os.environ["JOBAGENT_SLOW"] = "junk"  # garbage -> normal speed
        assert filler._pace() == 1.0
    finally:
        os.environ.pop("JOBAGENT_SLOW", None)
        if saved is not None:
            os.environ["JOBAGENT_SLOW"] = saved


class _FakeLoc:
    def __init__(self, texts):
        self._texts = texts

    def count(self):
        return len(self._texts)

    def nth(self, i):
        return _FakeOne(self._texts[i])


class _FakeOne:
    def __init__(self, text):
        self._text = text

    def is_visible(self):
        return self._text is not None  # None mimics a hidden node

    def inner_text(self):
        return self._text


class _FakeRoot:
    """Returns errors only for the first error selector; empty for the rest."""
    def __init__(self, texts):
        self._texts = texts
        self._served = False

    def locator(self, sel):
        if not self._served:
            self._served = True
            return _FakeLoc(self._texts)
        return _FakeLoc([])


def test_collect_errors_dedups_and_skips_hidden():
    root = _FakeRoot(["Error: Required", "Error: Required", None, "Bad phone"])
    errs = filler._collect_errors(root)
    assert errs == ["Error: Required", "Bad phone"]


def test_compliance_answer_maps_citi_questions():
    yes_qs = [
        "Are you legally authorized to work in the country where the position is located?",
        "Can you, within the time period prescribed by law, submit verification of both "
        "your identity and authorization to work in the United States?",
        "Are you at least 18 years of age?",
    ]
    for q in yes_qs:
        assert filler._compliance_answer(q) == "Yes", q
    no_qs = [
        "Will you require sponsorship for employment?",
        "Have you ever been employed by KPMG?",
        "Are you a referral or relative of a current Senior Government Official?",
        "Are you a referral of a current Senior Commercial Person?",
        "Have you ever served in the Armed Forces of the United States?",
        "",
    ]
    for q in no_qs:
        assert filler._compliance_answer(q) == "No", q
