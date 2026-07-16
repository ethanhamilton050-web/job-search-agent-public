"""Tests for jobagent.linkcheck: per-ATS URL shape validation (ISSUES.md item D).

No network calls anywhere here -- pure shape/regex checks.
"""
from jobagent.linkcheck import looks_like_application_url


def test_greenhouse_canonical_url_passes():
    assert looks_like_application_url(
        "https://boards.greenhouse.io/sofi/jobs/12345", "greenhouse:sofi")


def test_greenhouse_job_boards_subdomain_passes():
    # Real bug caught by testing against the actual 1058-listing DB, not just
    # hand-picked examples: a real, currently-live Affirm posting used
    # job-boards.greenhouse.io (Greenhouse's newer platform), and the original
    # boards-only regex wrongly rejected it as "known-wrong shape."
    assert looks_like_application_url(
        "https://job-boards.greenhouse.io/affirm/jobs/7764115003", "greenhouse:affirm")


def test_greenhouse_marketing_site_fails():
    # the exact historical bug (Fireblocks): absolute_url pointed at a page with no form
    assert not looks_like_application_url(
        "https://fireblocks.com/careers", "greenhouse:fireblocks")


def test_lever_url_passes_even_with_short_placeholder_id():
    # tests/test_apply_routing.py already treats this shape as valid -- a strict
    # UUID-length check would have false-rejected it, so this one must too.
    assert looks_like_application_url("https://jobs.lever.co/carta/abc-def/apply",
                                      "lever:carta")


def test_lever_bare_domain_with_no_posting_fails():
    assert not looks_like_application_url("https://jobs.lever.co/carta", "lever:carta")


def test_ashby_url_passes():
    assert looks_like_application_url("https://jobs.ashbyhq.com/anthropic/xyz",
                                      "ashby:anthropic")


def test_workday_url_passes():
    assert looks_like_application_url(
        "https://citi.wd5.myworkdayjobs.com/en-US/2/job/some-role/123", "workday:citi")


def test_workday_wrong_domain_fails():
    assert not looks_like_application_url("https://citi.com/careers/job/123", "workday:citi")


def test_empty_url_fails():
    assert not looks_like_application_url("", "greenhouse:sofi")


def test_unrecognized_source_is_allowed_through():
    # we don't have a pattern for it -- never invent a rule we have no evidence for
    assert looks_like_application_url("https://example.com/anything", "somenewats:acme")
    assert looks_like_application_url("https://example.com/anything", "")


def test_workday_bare_careers_site_without_job_path_fails():
    # A listing missing externalPath degrades to exactly this shape (ISSUES G) --
    # the one ATS where the unattended queue launches a real browser, so a
    # careers-HOME link must not pass as an application link.
    assert not looks_like_application_url(
        "https://pnc.wd5.myworkdayjobs.com/External", "workday:pnc")
    assert not looks_like_application_url(
        "https://pnc.wd5.myworkdayjobs.com/en-US/External", "workday:pnc")


def test_workday_job_link_without_locale_passes():
    # the scanner builds root/site + externalPath (no locale segment)
    assert looks_like_application_url(
        "https://pnc.wd5.myworkdayjobs.com/External/job/Pittsburgh-PA/Analyst_R-123",
        "workday:pnc")
