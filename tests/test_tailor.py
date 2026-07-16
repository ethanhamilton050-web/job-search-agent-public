"""Tests for the resume number-lock validator — the safety-critical piece."""
from jobagent import tailor
from jobagent.models import ResumeProfile

MASTER = (
    "Increased revenue by 35% over 2 years, managing a $1.2M budget.\n"
    "Led a team of 8 at Acme Corp from 2019 to 2021.\n"
    "Cut processing time by 3x using Python and SQL."
)


def test_verbiage_only_edit_passes():
    tailored = (
        "Drove a 35% revenue increase across 2 years while owning a $1.2M budget.\n"
        "Directed a team of 8 at Acme Corp from 2019 to 2021.\n"
        "Reduced processing time 3x with Python and SQL."
    )
    res = tailor.validate(MASTER, tailored)
    assert res.ok, res.report()


def test_rephrased_opening_verbs_do_not_warn():
    """Regression (found live, 2026-07-09, by an overnight adversarial audit): the
    old 3-verb stop list only covered this project's own test fixture, so this
    exact reword (only the opening verbs changed) used to trigger a "possibly
    invented employer/tool" warning for every rephrased verb -- on nearly every
    tailoring pass, since rewording the opening verb is exactly what tailoring
    is for. res.ok already passed before (warnings don't block), so this checks
    the warnings themselves, not just ok."""
    tailored = (
        "Drove a 35% revenue increase across 2 years while owning a $1.2M budget.\n"
        "Directed a team of 8 at Acme Corp from 2019 to 2021.\n"
        "Reduced processing time 3x with Python and SQL."
    )
    res = tailor.validate(MASTER, tailored)
    assert res.ok
    assert not res.warnings, res.report()


def test_reformatted_number_still_passes():
    # $1.2M == $1,200,000 ; 35% == 35 percent ; 3x unchanged
    tailored = (
        "Increased revenue by 35 percent over 2 years, managing a $1,200,000 budget.\n"
        "Led a team of 8 at Acme Corp from 2019 to 2021.\n"
        "Cut processing time by 3x using Python and SQL."
    )
    res = tailor.validate(MASTER, tailored)
    assert res.ok, res.report()


def test_space_separated_unit_shorthand_still_passes():
    """Regression (found live, 2026-07-09, by an overnight adversarial audit):
    "$5 mm" (space before the unit) used to fail to scale at all, so rewording
    it to the equivalent "$5,000,000" was wrongly flagged as both a dropped
    fact ($5) and an added one ($5,000,000)."""
    res = tailor.validate("managed a $5 mm budget", "managed a $5,000,000 budget")
    assert res.ok, res.report()
    res2 = tailor.validate("grew revenue 35 % last year", "grew revenue by 35% last year")
    assert res2.ok, res2.report()


def test_space_separated_unit_shorthand_still_detects_a_real_change():
    # sanity: the space-tolerance fix must not make the lock LESS strict --
    # an actually-changed amount is still caught.
    res = tailor.validate("managed a $5 mm budget", "managed a $6,000,000 budget")
    assert not res.ok
    assert any("5,000,000" in e for e in res.errors)


def test_changed_number_fails():
    tailored = MASTER.replace("35%", "45%")
    res = tailor.validate(MASTER, tailored)
    assert not res.ok
    assert any("dropped or altered" in e or "New/changed" in e for e in res.errors)


def test_added_number_fails():
    tailored = MASTER + "\nSaved $500K extra."
    res = tailor.validate(MASTER, tailored)
    assert not res.ok
    assert any("New/changed numbers" in e for e in res.errors)


def test_dropped_metric_fails():
    tailored = MASTER.replace("managing a $1.2M budget", "managing the budget")
    res = tailor.validate(MASTER, tailored)
    assert not res.ok
    assert any("dropped or altered" in e for e in res.errors)


def test_new_employer_warns():
    tailored = MASTER.replace("Acme Corp", "Acme Corp and Globex")
    res = tailor.validate(MASTER, tailored)
    assert any("Globex" in w for w in res.warnings)


def test_known_skill_not_warned():
    prof = ResumeProfile(skills=["Python", "SQL", "Tableau"])
    tailored = MASTER + "\nBuilt dashboards in Tableau."
    res = tailor.validate(MASTER, tailored, prof)
    # Tableau is a known skill, so it should not be flagged as a new entity.
    assert not any("Tableau" in w for w in res.warnings)


def test_extract_facts_canonicalization():
    assert tailor.extract_facts("$1.2M") == tailor.extract_facts("$1,200,000")
    assert tailor.extract_facts("35%") == tailor.extract_facts("35 percent")


def test_dropping_dollar_sign_fails():
    # "$1.2M budget" -> "1.2M budget" changes meaning; must be caught.
    res = tailor.validate("managed a $1.2M budget", "managed a 1.2M budget")
    assert not res.ok


def test_dollar_amount_reworded_to_words_passes():
    # "$2M" and "2 million dollars" are the same fact (currency preserved).
    assert tailor.extract_facts("$2M") == tailor.extract_facts("2 million dollars")
    res = tailor.validate("a $2M budget", "a budget of 2 million dollars")
    assert res.ok, res.report()


def test_apostrophe_year_reformat_passes():
    res = tailor.validate("Analyst ('19-'21)", "Analyst (2019-2021)")
    assert res.ok, res.report()


def test_changed_decade_fails():
    # Regression guard: a non-year apostrophe number ('90s) must not vanish.
    res = tailor.validate("built on a '90s stack", "built on a '80s stack")
    assert not res.ok


def test_word_number_change_warns_not_blocks():
    # spelled-out quantities aren't hard-locked, but must warn the human
    res = tailor.validate("over three years", "over five years")
    assert res.ok                       # verbiage edit, not a hard fail
    assert any("quantity words changed" in w for w in res.warnings)


def test_dropped_large_number_renders_with_commas():
    # error text must be readable, not lossy scientific notation (1.23457e+06)
    res = tailor.validate("traced 1,234,567 transactions", "traced transactions")
    assert not res.ok
    assert "1,234,567" in " ".join(res.errors)


def test_dropped_minus_sign_is_a_changed_fact():
    """ISSUES G: 'grew headcount by -12' reworded to 'cut headcount by 12'
    used to pass as unchanged -- the sign IS the fact."""
    r = tailor.validate("Grew headcount by -12 during restructuring",
                        "Cut headcount by 12 during restructuring")
    assert not r.ok


def test_negative_number_preserved_through_reword_passes():
    r = tailor.validate("Delivered returns of -12% in Q1",
                        "Q1 returns came to -12%")
    assert r.ok


def test_date_range_hyphen_is_not_a_negative_number():
    facts = tailor.extract_facts("Analyst, 2019-2021, led coverage")
    assert tailor.Fact("year", 2019.0) in facts
    assert tailor.Fact("year", 2021.0) in facts
    assert all(f.value > 0 for f in facts)


def test_range_and_bullet_dashes_are_not_negative():
    # "10-15%" is a range, "- 12%" is a list bullet -- neither is signed
    facts = tailor.extract_facts("cut costs 10-15%\n- 12% budget reduction")
    assert tailor.Fact("pct", 15.0) in facts
    assert tailor.Fact("pct", 12.0) in facts
    assert all(f.value > 0 for f in facts)
