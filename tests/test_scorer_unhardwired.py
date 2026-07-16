"""F1 (un-hardwire the search) + F12 (neutral first-run config) guards.

Before F1 the metro list, the senior/engineer exclusions and the remote drop were
baked into scorer.py, so anyone whose search differed from Ethan's saw wrong jobs
or a blank screen. These tests prove the same filters now follow the customer's
config: a remote seeker, a senior candidate, an engineer, and someone outside the
NJ metros all get the right jobs.
"""
from jobagent.scorer import location_ok, qualified


# --- F1: remote seeker is no longer silently excluded -------------------------
def test_remote_included_when_remote_ok():
    t = {"locations": ["Remote"], "remote_ok": True}
    assert location_ok("Remote - US", False, t)
    assert location_ok("Anywhere", True, t)


def test_remote_excluded_when_not_remote_ok():
    t = {"locations": ["New York, NY"], "remote_ok": False}
    assert not location_ok("Remote - US", False, t)


# --- F1: a customer outside the NJ metros isn't handed a blank screen ----------
def test_no_locations_configured_shows_any_us_location():
    t = {"locations": [], "remote_ok": False}
    assert location_ok("Austin, TX", False, t)
    assert location_ok("San Francisco, CA", False, t)
    assert not location_ok("Toronto, Canada", False, t)  # foreign still excluded


def test_custom_metros_drive_the_filter():
    t = {"locations": ["Austin, TX", "Denver, CO"], "remote_ok": False}
    assert location_ok("Austin, TX", False, t)
    assert location_ok("Denver, CO", False, t)
    assert not location_ok("New York, NY", False, t)  # not this customer's metro


# --- F1: a senior candidate / an engineer see their own roles -----------------
def test_no_exclusions_keeps_senior_and_engineer_roles():
    assert qualified("Senior Director of Finance")          # no targets at all
    assert qualified("Staff Software Engineer", {})
    assert qualified("VP, Data", {"exclude_title_terms": []})


def test_customer_can_set_their_own_exclusions():
    t = {"exclude_title_terms": ["intern", "junior"]}
    assert not qualified("Marketing Intern", t)
    assert not qualified("Junior Accountant", t)
    assert qualified("Senior Financial Analyst", t)  # senior NOT excluded for them


def test_space_scoped_term_does_not_substring_match():
    # " ml " must not fire on "html"/"formula" -- the space padding is what scopes it.
    t = {"exclude_title_terms": [" ml "]}
    assert qualified("HTML Formula Analyst", t)
    assert not qualified("ML Research Analyst", t)


# --- F12: first run seeds a neutral, non-clobbering starter config -------------
def test_ensure_config_seeds_when_missing(tmp_path):
    from jobagent import config
    cfg = tmp_path / "config.json"
    example = tmp_path / "config.example.json"
    example.write_text('{"targets": {"titles": []}}', encoding="utf-8")
    config.ensure_config(config_path=cfg, example_path=example)
    assert cfg.exists()
    assert cfg.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")


def test_ensure_config_never_overwrites_existing(tmp_path):
    from jobagent import config
    cfg = tmp_path / "config.json"
    example = tmp_path / "config.example.json"
    cfg.write_text('{"MINE": true}', encoding="utf-8")
    example.write_text('{"targets": {"titles": []}}', encoding="utf-8")
    config.ensure_config(config_path=cfg, example_path=example)
    assert cfg.read_text(encoding="utf-8") == '{"MINE": true}'  # untouched
