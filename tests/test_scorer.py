"""Tests for fit scoring and listing dedup."""
from jobagent.config import DEFAULT_CONFIG
from jobagent.models import Listing, ResumeProfile


def _cfg():
    import json
    return json.loads(json.dumps(DEFAULT_CONFIG))


def test_score_rewards_skill_and_title_match():
    from jobagent.scorer import score_listing

    prof = ResumeProfile(
        skills=["Python", "SQL", "Excel"],
        targets={"titles": ["Data Analyst"], "keywords": ["dashboard"],
                 "locations": ["Remote"], "remote_ok": True},
    )
    good = Listing(
        title="Data Analyst", company="X",
        description="Build dashboards with Python and SQL and Excel.",
    )
    bad = Listing(
        title="Welder", company="Y",
        description="Weld steel beams on site.",
    )
    sg, _ = score_listing(good, prof, _cfg())
    sb, _ = score_listing(bad, prof, _cfg())
    assert sg > sb
    assert sg > 50


def test_listing_id_dedup_by_url():
    a = Listing(title="A", company="C", description="d", url="https://x.com/job/1")
    b = Listing(title="A different title", company="C2", description="d2",
                url="https://x.com/job/1")
    assert a.id == b.id  # same URL -> same id


def test_listing_id_without_url():
    a = Listing(title="Analyst", company="Acme", description="d", location="NYC")
    b = Listing(title="Analyst", company="Acme", description="d", location="NYC")
    assert a.id == b.id  # identical content -> same id


def test_listing_id_differs_on_description_without_url():
    # Two distinct pastes that share a default title/company must NOT collide,
    # or the second would silently overwrite the first in the DB.
    a = Listing(title="(pasted job)", company="(unknown)", description="role one")
    b = Listing(title="(pasted job)", company="(unknown)", description="role two")
    assert a.id != b.id


def test_seniority_penalty_is_whole_word():
    from jobagent.scorer import score_listing

    prof = ResumeProfile(
        skills=["Python", "SQL", "Excel"],
        targets={"titles": ["Analyst"], "keywords": ["dashboard"], "remote_ok": True},
    )
    desc = "Build dashboards with Python and SQL and Excel."
    # "Staff Analyst" IS senior -> penalized; "Stafford Analyst" must NOT be.
    senior, _ = score_listing(Listing(title="Staff Analyst", company="X", description=desc), prof, _cfg())
    not_senior, _ = score_listing(Listing(title="Stafford Analyst", company="X", description=desc), prof, _cfg())
    assert not_senior > senior


def test_salary_floor_flag():
    from jobagent.scorer import score_listing

    prof = ResumeProfile(skills=["Excel"], targets={"salary_floor": 60000})
    low = Listing(title="Analyst", company="X", description="Excel", salary="$40,000 - $50,000")
    _, reasons = score_listing(low, prof, _cfg())
    assert "below salary floor" in reasons


def test_work_auth_flag():
    from jobagent.scorer import score_listing

    prof = ResumeProfile(
        skills=["Excel"],
        targets={"work_authorization": "US authorized / no sponsorship needed"},
    )
    job = Listing(title="Analyst", company="X",
                  description="Must be eligible for a security clearance. Excel required.")
    _, reasons = score_listing(job, prof, _cfg())
    assert "work-auth" in reasons


def test_short_skills_dont_substring_match():
    # 'c'/'r'/'go' must not match inside 'company'/'are'/'goals' (word-boundary fix)
    from jobagent.scorer import score_listing

    prof = ResumeProfile(skills=["C", "R", "Go"],
                         targets={"titles": [], "keywords": [], "locations": []})
    listing = Listing(title="Analyst", company="X",
                      description="Our company values are central to our goals.")
    _, reasons = score_listing(listing, prof, _cfg())
    assert "skills:" not in reasons  # no spurious skill hits


def test_real_skill_word_still_matches():
    from jobagent.scorer import score_listing

    prof = ResumeProfile(skills=["Python"],
                         targets={"titles": [], "keywords": [], "locations": []})
    listing = Listing(title="Dev", company="X", description="We use Python daily.")
    _, reasons = score_listing(listing, prof, _cfg())
    assert "python" in reasons.lower()


# Ethan's tri-state metro allowlist, now living in config instead of hardwired in
# scorer.py (F1). Tests below assert his behavior is preserved when his config is
# supplied; test_scorer_unhardwired.py covers other customers' configs.
_NJ_METRO = [
    ", nj", ", ny", ", pa",
    "new jersey", "new york", "philadelphia", "pittsburgh", "nyc",
    "manhattan", "brooklyn", "chester", "morristown", "parsippany",
    "princeton", "newark", "jersey city", "hoboken", "edison",
    "paramus", "saddle brook", "fairfield", "philly",
]


def test_location_filter():
    from jobagent.scorer import location_ok
    t = {"locations": _NJ_METRO, "remote_ok": False}
    # remote excluded entirely (user wants on-site)
    assert not location_ok("New York, NY", True, t)
    assert not location_ok("Remote", True, t)
    assert not location_ok("Remote - US", False, t)        # "remote" in the text
    # foreign excluded
    assert not location_ok("Toronto, Canada", False, t)
    assert not location_ok("London", False, t)
    # target metros kept (on-site)
    assert location_ok("New York, NY", False, t)
    assert location_ok("Jersey City, NJ", False, t)
    assert location_ok("Chester, NJ", False, t)
    assert location_ok("Philadelphia, PA", False, t)
    assert location_ok("Pittsburgh, PA", False, t)
    # far US on-site / national / unknown excluded
    assert not location_ok("Frisco, TX", False, t)
    assert not location_ok("San Francisco, CA", False, t)
    assert not location_ok("United States", False, t)
    assert not location_ok("", False, t)


def test_location_mismatch_reason_agrees_with_location_ok():
    # Jersey City is accepted via the ", nj" token in the configured metro list --
    # the "location mismatch" reason must use the same location_ok rule, not a
    # narrower one that flags jobs the app otherwise treats as wanted (and would
    # normally show by default).
    from jobagent.scorer import score_listing

    prof = ResumeProfile(skills=["Excel"], targets={"locations": _NJ_METRO})
    wanted = Listing(title="Analyst", company="X", description="Excel required.",
                      location="Jersey City, NJ")
    _, reasons = score_listing(wanted, prof, _cfg())
    assert "location mismatch" not in reasons

    unwanted = Listing(title="Analyst", company="X", description="Excel required.",
                        location="Frisco, TX")
    _, reasons2 = score_listing(unwanted, prof, _cfg())
    assert "location mismatch" in reasons2


# Ethan's early-career finance exclusions, now config-driven (F1).
_EXCLUDE = {"exclude_title_terms": [
    "senior", "sr.", " sr ", " sr,", "staff ", "principal", " lead ", " lead,",
    "lead ", "director", "vp ", "vp,", "vice president", "head of", "chief",
    "manager", " mgr",
    "engineer", "developer", "devops", "site reliability", " sre",
    "security analyst", "information security", "cybersecurity", "network",
    "machine learning", " ml ", "full stack", "full-stack", "frontend",
    "front-end", "backend", "back-end", "ios ", "android", "embedded",
    "firmware", "account executive", "sales representative",
]}


def test_qualified_filter():
    # Ethan's exclusions supplied via config -> his old hardwired behavior preserved.
    from jobagent.scorer import qualified
    assert qualified("Financial Analyst", _EXCLUDE)
    assert qualified("Audit Associate", _EXCLUDE)
    assert qualified("Capital Operations Analyst", _EXCLUDE)
    # senior / wrong-field hidden
    assert not qualified("Senior IT Analyst", _EXCLUDE)
    assert not qualified("Information Security Analyst", _EXCLUDE)
    assert not qualified("Finance Systems Engineer, Revenue", _EXCLUDE)
    assert not qualified("Staff Credit Policy Analyst", _EXCLUDE)
    assert not qualified("Software Engineer", _EXCLUDE)
    assert not qualified("Director of Finance", _EXCLUDE)
    assert not qualified("Enterprise Account Executive", _EXCLUDE)
