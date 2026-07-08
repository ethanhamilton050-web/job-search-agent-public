"""Tests for resume parsing — guards the regression where glyph-less bullet
lines were each turned into a separate empty role (no company, no dates)."""
from jobagent.profile import parse_text

# A resume in the real-world format this tool targets: ALL-CAPS section headers,
# "Company - Location <tab> Dates" or standalone-company then "Title <tab> Dates",
# and bullets with NO leading glyph (just plain lines).
SAMPLE = """Jane Analyst
555-555-0100 | jane@example.com
EDUCATION
Some State University - Trenton, NJ
B.S. in Finance | May 2021
TECHNICAL SKILLS
Excel | Python | Bloomberg Terminal | Audit
PROFESSIONAL EXPERIENCE
Big Bank Corp - New York, NY\tJan 2023 - April 2026
Financial Analyst
Built financial models covering 12 portfolio companies
Reduced monthly close time by 30% through automation
Student Investment Fund - Some State University
Fund Manager\tSep 2020 - May 2021
Managed a 9-person analyst team across 4 funds
Pitched ideas that were added to the portfolio
"""


def test_parses_expected_number_of_roles():
    prof = parse_text(SAMPLE)
    assert len(prof.experience) == 2  # not one-role-per-bullet-line


def test_company_and_dates_are_populated():
    prof = parse_text(SAMPLE)
    first = prof.experience[0]
    assert first.title == "Financial Analyst"
    assert "Big Bank Corp" in first.company
    assert first.dates == "Jan 2023 - April 2026"
    assert len(first.bullets) == 2


def test_standalone_company_line_attaches_to_following_title():
    prof = parse_text(SAMPLE)
    second = prof.experience[1]
    assert second.title == "Fund Manager"
    assert "Student Investment Fund" in second.company
    assert second.dates == "Sep 2020 - May 2021"
    assert len(second.bullets) == 2


def test_bullets_are_not_promoted_to_roles():
    prof = parse_text(SAMPLE)
    # Every parsed role must have a real title (no bullet sentence leaked in as a
    # title, and no empty placeholder roles).
    for role in prof.experience:
        assert role.title and not role.title.endswith("automation")


def test_skills_section_parsed():
    prof = parse_text(SAMPLE)
    assert "Excel" in prof.skills
    assert "Bloomberg Terminal" in prof.skills


def test_comma_style_company_header_attaches():
    # 'Company, City, ST' header (no dash, no org suffix) must not be dropped.
    text = (
        "PROFESSIONAL EXPERIENCE\n"
        "Acme Capital, New York, NY\n"
        "Analyst - Equity Research\tJan 2020 - Mar 2022\n"
        "Built DCF models\n"
        "Ran comps\n"
    )
    prof = parse_text(text)
    assert len(prof.experience) == 1
    role = prof.experience[0]
    assert "Acme Capital" in role.company
    assert role.title == "Analyst - Equity Research"
    assert len(role.bullets) == 2
