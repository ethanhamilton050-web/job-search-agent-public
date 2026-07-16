"""Offline checks for the filler's pure helpers (date/education parsing).

The browser-driving code is host-only (needs Playwright + real logins), so the
only thing worth a unit test here is the fragile text->fields parsing.
"""
import os

from jobagent import qbank
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


def test_questionnaire_answer_grounds_universal_facts_and_parks_the_rest(tmp_path, monkeypatch):
    """The honesty fix (replaces the old _compliance_answer): _questionnaire_answer only
    returns a Yes/No it can PROVE — the human's own remembered answer, or a
    guardrail-grounded universal fact — and returns "" ('leave it for the human') for
    everything else, instead of blind-defaulting the unknown to 'No' and stating
    falsehoods like 'No, I don't hold a FINRA license' with zero human review."""
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    facts = {"work_authorized": True, "needs_sponsorship": False,
             "is_over_18": True, "is_veteran": False}

    # Provable from the answer bank via the guardrail -> answered truthfully.
    assert filler._questionnaire_answer(
        "Are you legally authorized to work in the United States?", facts) == "Yes"
    assert filler._questionnaire_answer("Are you at least 18 years of age?", facts) == "Yes"
    assert filler._questionnaire_answer(
        "Will you require sponsorship for employment?", facts) == "No"
    assert filler._questionnaire_answer(
        "Have you ever served in the Armed Forces of the United States?", facts) == "No"

    # Unprovable / employer-specific -> "" (parked for the human), NOT a fabricated 'No'.
    for q in ["Do you currently hold an active FINRA securities license?",
              "Have you ever been employed by KPMG?",
              "Have you ever been terminated or asked to resign?",
              ""]:
        assert filler._questionnaire_answer(q, facts) == "", q

    # Live PNC noise (button placeholder + injected validation error) is stripped first.
    noisy = "Are you 18 years of age or older?* Select One Error: The field is required"
    assert filler._questionnaire_answer(noisy, facts) == "Yes"

    # The human's own remembered answer (qbank) wins over everything.
    qbank.save({"Have you ever been employed by KPMG?": "No"})
    assert filler._questionnaire_answer("have you ever been employed by kpmg", facts) == "No"

    # A 'universal' question whose backing field is missing still routes to the human.
    assert filler._questionnaire_answer(
        "Have you ever served in the Armed Forces?", {"work_authorized": True}) == ""


def test_selfid_value_defaults_to_declining_language_per_field():
    """The self-ID / EEO mapper drives the most sensitive fields on any application;
    a quiet regression here would mis-answer race/gender/veteran. (Audit U11.)"""
    # No preference (or an explicit "decline") -> each field's real decline phrasing.
    assert filler._selfid_value("decline", "gender") == "I do not wish to answer"
    assert filler._selfid_value("", "race") == "I do not wish to self-identify"
    assert filler._selfid_value(None, "veteran") == "I am not a veteran"
    # An explicit real preference is passed through untouched.
    assert filler._selfid_value("Female", "gender") == "Female"
    # An unknown field kind falls back to the literal "decline" (never guesses a value).
    assert filler._selfid_value("decline", "unknown") == "decline"


def test_degree_aliases_mba_never_matches_master_of_science():
    """Honesty-contract adversarial case (near-miss): an MBA must offer MBA forms,
    and NO want may substring-match a tenant's 'Master of Science' option —
    that click asserts a degree Ethan doesn't hold (the license/education class)."""
    wants = filler._degree_aliases("MBA")
    assert "MBA" in wants and "Master of Business Administration" in wants
    assert not [w for w in wants if w.lower() in "master of science"], wants


def test_degree_aliases_jd_never_matches_phd():
    """'Juris Doctor' contains 'doctor' — it must NOT fall through to the Ph.D.
    branch and click 'Ph.D.'/'Doctorate' (a credential class the holder lacks)."""
    for spelled in ("J.D.", "JD", "Juris Doctor (J.D.)"):
        wants = filler._degree_aliases(spelled)
        assert "Juris Doctor" in wants, (spelled, wants)
        assert "Ph.D." not in wants and "Doctorate" not in wants, (spelled, wants)
        assert not [w for w in wants if w.lower() in "doctor of philosophy"], wants
