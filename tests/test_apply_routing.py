"""apply must route Workday URLs to the wizard, everything else to the generic filler."""
from jobagent.applier import is_workday


def test_workday_urls_detected():
    assert is_workday("https://acme.wd5.myworkdayjobs.com/External/job/123")
    assert is_workday("https://www.myworkdayjobs.com/en-US/foo")


def test_non_workday_urls_use_generic():
    assert not is_workday("https://boards.greenhouse.io/sofi/jobs/123")
    assert not is_workday("https://jobs.lever.co/carta/abc-def/apply")
    assert not is_workday("https://jobs.ashbyhq.com/anthropic/xyz")
    assert not is_workday("")
