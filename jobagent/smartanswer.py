"""Job-SPECIFIC screening answers that can't be a fixed remembered string.

Most screening answers are reusable as-is (qbank). Two aren't — they depend on the
job in front of you, so they're computed at fill time from the job's context
(`ans["_job"]`, threaded in by the apply command):

  - SALARY expectations: the human stores a STRATEGY once (below / average / above);
    we turn it into a number from THIS job's posted range. (salary.py does the math.)
  - RESIDENCE ("do you currently reside near the job's location?"): answered truthfully
    from the human's home area vs the job's state — never faked to a flattering "Yes".

resolve() returns a computed answer string, or "" meaning 'not one of these / can't
compute' so the caller falls back to the normal qbank -> guardrail -> park path. It is
tried FIRST in _questionnaire_answer so a stored strategy word ("average") becomes the
number, not a literal typed into the box.
"""
from __future__ import annotations

import re

from . import qbank, salary

_SALARY_RE = re.compile(
    r"salary|compensation|pay\s+expectation|expected\s+(?:salary|pay|comp)"
    r"|desired\s+(?:salary|pay|compensation)", re.I)
_RESIDENCE_RE = re.compile(
    r"(?:currently\s+)?resid\w*\s+near|live\s+near|near\s+one\s+of\s+the\s+location", re.I)

# Ethan's home commute area (from his NY/NJ profile). A job in these states counts as
# "near"; anything else (e.g. Pittsburgh, PA) truthfully does not.
# ponytail: a hardcoded home region, correct for his tri-state search; the general-
# customer upgrade is a real home-address field + a distance/geocode check.
_HOME_STATE_NAMES = ("new jersey", "new york", "connecticut")
_HOME_STATE_CODES = ("nj", "ny", "ct")


def is_salary_question(q: str) -> bool:
    """True for a salary/compensation-expectation question (dashboard renders a dropdown)."""
    return bool(_SALARY_RE.search(q or ""))


def is_residence_question(q: str) -> bool:
    """True for a 'do you reside near the job?' question (auto-answered, no input needed)."""
    return bool(_RESIDENCE_RE.search(q or ""))


def needs_computed_salary(question: str) -> bool:
    """True when this is a salary question the human answered with a STRATEGY word
    (below/average/above). Its box must get a computed NUMBER or be parked — never the
    literal word. The caller uses this to suppress the plain-qbank fallback when
    resolve() couldn't produce a number (no posted range and no estimate), so "average"
    never lands in a real salary field."""
    return is_salary_question(question) and \
        qbank.answer(question).strip().lower() in salary.STRATEGIES


def _in_home_area(location: str) -> bool:
    loc = (location or "").lower()
    if any(name in loc for name in _HOME_STATE_NAMES):
        return True
    return any(re.search(rf"\b{code}\b", loc) for code in _HOME_STATE_CODES)


def resolve(question: str, ans: dict) -> str:
    """A computed answer for a salary/residence question, else "" (caller falls back)."""
    q = question or ""
    job = ans.get("_job") or {}

    if _RESIDENCE_RE.search(q):
        loc = job.get("location") or ""
        if not loc:
            return ""                     # no location to judge -> let it park, don't guess
        return "Yes" if _in_home_area(loc) else "No"

    if _SALARY_RE.search(q):
        strategy = qbank.answer(question).strip().lower()
        if strategy not in salary.STRATEGIES:
            return ""                     # human typed a literal (or nothing) -> normal path
        rng = job.get("salary_range")     # (lo, hi) parsed/estimated by the caller, or None
        if not rng:
            return ""                     # no range and no estimate -> park for the human
        return str(salary.pick(rng[0], rng[1], strategy))

    return ""


if __name__ == "__main__":  # ponytail: self-check, no framework
    import tempfile
    from pathlib import Path
    qbank.STORE = Path(tempfile.mkdtemp()) / "s.json"

    # Residence: truthful from home area
    assert resolve("Do you currently reside near one of the locations required for this job?",
                   {"_job": {"location": "Pittsburgh Pennsylvania United States"}}) == "No"
    assert resolve("Do you currently reside near one of the locations required for this job?",
                   {"_job": {"location": "New York New York United States"}}) == "Yes"
    assert resolve("Do you reside near the location?", {"_job": {"location": ""}}) == ""

    # Salary: strategy word -> number from the job's range
    qbank.save({"Please provide your salary expectations for this position.": "average"})
    assert resolve("Please provide your salary expectations for this position.",
                   {"_job": {"salary_range": (80000, 100000)}}) == "90000"
    # No strategy stored yet -> "" so it parks / normal path
    qbank.STORE = Path(tempfile.mkdtemp()) / "s2.json"
    assert resolve("Please provide your salary expectations for this position.",
                   {"_job": {"salary_range": (80000, 100000)}}) == ""
    # Not a smart question -> ""
    assert resolve("Are you 18 or older?", {"_job": {}}) == ""
    print("smartanswer self-check ok")
