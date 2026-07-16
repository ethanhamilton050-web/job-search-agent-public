"""Tests for jobagent.matchcoach: guarded resume-bullet rewrite suggestions.

No network: rewrite_fn is always a plain fake, same pattern as
tests/test_screening.py's ai_answer_fn fakes.
"""
import pytest

from jobagent.matchcoach import _parse_json_object, suggest_all, suggest_rewrite
from jobagent.models import ExperienceItem, ResumeProfile


def _profile(bullets, skills=("Excel", "Python")):
    return ResumeProfile(
        skills=list(skills),
        experience=[ExperienceItem(company="Acme", title="Analyst", dates="2020-2023",
                                    bullets=bullets)],
    )


def test_parse_json_object_tolerates_markdown_fences():
    """Regression (found live, 2026-07-09, by an overnight adversarial audit):
    Ollama/gemma models commonly wrap a requested JSON reply in ```json fences
    even when told "reply with ONLY JSON" -- a raw json.loads() on the whole
    reply used to raise ValueError every time that happened, silently returning
    None for every single bullet."""
    fenced = '```json\n{"trait": "leadership", "rewrite": "Led the team."}\n```'
    assert _parse_json_object(fenced) == {"trait": "leadership", "rewrite": "Led the team."}


def test_parse_json_object_tolerates_preamble_text():
    text = 'Sure, here is the JSON:\n{"trait": "leadership", "rewrite": "Led the team."}'
    assert _parse_json_object(text) == {"trait": "leadership", "rewrite": "Led the team."}


def test_parse_json_object_raises_on_no_object():
    with pytest.raises(ValueError):
        _parse_json_object("no json here at all")


def test_honest_rewrite_is_returned():
    rewritten = "Built financial models in Excel for quarterly forecasting."
    honest = lambda bullet, jd, traits: (rewritten, "forecasting")
    prof = _profile(["Built financial models in Excel."])
    s = suggest_rewrite("Built financial models in Excel.", "jd text", ["forecasting"],
                        prof, rewrite_fn=honest)
    assert s == {"original": "Built financial models in Excel.",
                 "suggested": rewritten, "for_trait": "forecasting", "cautions": []}


def test_rewrite_that_invents_a_number_is_dropped():
    # The AI's rewrite claims a number that wasn't in the original -- never surfaced.
    fabricator = lambda bullet, jd, traits: (
        "Built financial models in Excel, saving the team 40% in time.", "efficiency")
    prof = _profile(["Built financial models in Excel."])
    s = suggest_rewrite("Built financial models in Excel.", "jd text", ["efficiency"],
                        prof, rewrite_fn=fabricator)
    assert s is None


def test_rewrite_citing_a_trait_not_offered_is_rejected():
    # Fabricated/wrong citation: the AI must only cite a trait it was actually given.
    liar = lambda bullet, jd, traits: ("Managed vendor relationships.", "leadership")
    prof = _profile(["Managed vendor relationships."])
    s = suggest_rewrite("Managed vendor relationships.", "jd text", ["forecasting"],
                        prof, rewrite_fn=liar)
    assert s is None


def test_whitespace_only_rewrite_is_rejected():
    # A blank/garbage "rewrite" must never surface just because it's a non-empty
    # string -- the guard boundary checks meaningfulness, not just truthiness.
    blank = lambda bullet, jd, traits: ("   ", traits[0])
    prof = _profile(["Built financial models in Excel."])
    s = suggest_rewrite("Built financial models in Excel.", "jd text", ["forecasting"],
                        prof, rewrite_fn=blank)
    assert s is None


def test_declining_ai_returns_none():
    declines = lambda bullet, jd, traits: None
    prof = _profile(["Built financial models in Excel."])
    s = suggest_rewrite("Built financial models in Excel.", "jd text", ["forecasting"],
                        prof, rewrite_fn=declines)
    assert s is None


def test_new_proper_noun_kept_as_caution_not_blocked():
    # New capitalized term (possible invented tool) is a warning, not a hard block --
    # same severity split tailor.py already uses for tailored resume text.
    suggestive = lambda bullet, jd, traits: (
        "Built financial models in Tableau and Excel.", "visualization")
    prof = _profile(["Built financial models in Excel."])
    s = suggest_rewrite("Built financial models in Excel.", "jd text", ["visualization"],
                        prof, rewrite_fn=suggestive)
    assert s is not None
    assert any("Tableau" in c for c in s["cautions"])


def test_suggest_all_caps_at_limit():
    always = lambda bullet, jd, traits: (bullet + " (rephrased)", traits[0])
    prof = _profile([f"Did task {i}." for i in range(10)])
    out = suggest_all(prof, "jd text", ["thing"], rewrite_fn=always, limit=3)
    assert len(out) == 3


def test_suggest_all_skips_bullets_the_ai_declines():
    def picky(bullet, jd, traits):
        return None if "skip" in bullet else (bullet + " (rephrased)", traits[0])
    prof = _profile(["Do the skip task.", "Built financial models in Excel."])
    out = suggest_all(prof, "jd text", ["thing"], rewrite_fn=picky, limit=5)
    assert len(out) == 1
    assert out[0]["original"] == "Built financial models in Excel."
    assert out[0]["company"] == "Acme"


def test_coach_traits_targets_what_the_jd_wants_and_resume_lacks():
    """ISSUES G: the coach's list is now JD-demanded terms missing from the
    RESUME (matching its own prompt: 'the posting wants these traits'), ordered
    by JD emphasis -- not the score display's capped/inverted 'missing' string."""
    from jobagent.matchcoach import coach_traits
    prof = _profile(["Built valuation models in Excel for coverage decisions."])
    jd = ("We need Bloomberg terminal experience. Bloomberg data feeds daily. "
          "Audit background a plus. Excel and valuation skills required.")
    keywords = ["bloomberg", "audit", "excel", "valuation", "python"]
    traits = coach_traits(prof, jd, keywords)
    # excel: already in the resume -> excluded. python: JD never asks -> excluded.
    # valuation: in resume -> excluded. bloomberg (2 JD hits) ranks above audit (1).
    assert traits == ["bloomberg", "audit"]


def test_coach_traits_word_boundary_safe():
    from jobagent.matchcoach import coach_traits
    prof = _profile(["Managed quarterly reporting."], skills=())
    # 'ai' must not match inside 'daily'; 'r' must not match inside 'reporting'
    traits = coach_traits(prof, "daily reporting cadence", ["ai", "r"])
    assert traits == []
