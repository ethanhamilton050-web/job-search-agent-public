"""Job-specific 'smart' screening answers: salary strategy -> number from the job's
range; residence answered truthfully from home area vs the job's state. Guards Ethan's
2026-07-13 design decisions (salary as below/avg/above; residence auto-answer truthful,
never a faked 'Yes')."""
from jobagent import qbank, smartanswer

SALARY_Q = "Please provide your salary expectations for this position."
RESIDE_Q = "Do you currently reside near one of the locations required for this job?"


def test_residence_truthful_no_for_far_job(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    assert smartanswer.resolve(RESIDE_Q,
                               {"_job": {"location": "Pittsburgh Pennsylvania United States"}}) == "No"


def test_residence_yes_for_home_area(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    assert smartanswer.resolve(RESIDE_Q,
                               {"_job": {"location": "New York New York United States"}}) == "Yes"
    assert smartanswer.resolve(RESIDE_Q,
                               {"_job": {"location": "Jersey City, NJ"}}) == "Yes"


def test_residence_parks_when_location_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    assert smartanswer.resolve(RESIDE_Q, {"_job": {"location": ""}}) == ""   # never guesses
    assert smartanswer.resolve(RESIDE_Q, {}) == ""                           # no job context


def test_salary_strategy_becomes_number(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({SALARY_Q: "above"})
    assert smartanswer.resolve(SALARY_Q, {"_job": {"salary_range": (80000, 100000)}}) == "100000"
    qbank.save({SALARY_Q: "average"})
    assert smartanswer.resolve(SALARY_Q, {"_job": {"salary_range": (80000, 100000)}}) == "90000"


def test_salary_without_strategy_falls_through(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    # nothing stored yet -> "" so the caller parks it (human sets a strategy)
    assert smartanswer.resolve(SALARY_Q, {"_job": {"salary_range": (80000, 100000)}}) == ""
    # a literal typed answer is NOT a strategy -> "" so the normal qbank path fills it
    qbank.save({SALARY_Q: "$88,000"})
    assert smartanswer.resolve(SALARY_Q, {"_job": {"salary_range": (80000, 100000)}}) == ""


def test_salary_with_strategy_but_no_range_parks(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({SALARY_Q: "average"})
    assert smartanswer.resolve(SALARY_Q, {"_job": {"salary_range": None}}) == ""


def test_non_smart_question_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    assert smartanswer.resolve("Are you 18 years of age or older?", {"_job": {}}) == ""


def test_needs_computed_salary_flags_strategy_answers(tmp_path, monkeypatch):
    # Guards the 2026-07-13 live bug: a strategy word ('average') must never fill the box
    # literally when no range is available — the caller parks instead.
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({SALARY_Q: "average"})
    assert smartanswer.needs_computed_salary(SALARY_Q) is True     # -> caller parks, no literal
    qbank.save({SALARY_Q: "$88,000"})
    assert smartanswer.needs_computed_salary(SALARY_Q) is False    # literal number -> fill it
    assert smartanswer.needs_computed_salary("Are you 18 or older?") is False
