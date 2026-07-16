"""Source adapters must survive null/odd fields the APIs really send."""
from jobagent.sources import greenhouse, lever, workday


def test_greenhouse_null_location_does_not_crash(monkeypatch):
    # Greenhouse really sends {"location": {"name": null}} — used to crash the whole scan
    monkeypatch.setattr(greenhouse, "get_json", lambda url: {
        "jobs": [{"title": "Analyst", "location": {"name": None},
                  "content": "desc", "absolute_url": "https://x/1"}]
    })
    out = greenhouse.fetch("acme")
    assert len(out) == 1 and out[0].location == ""


def test_greenhouse_forces_canonical_url_over_overridden_absolute_url(monkeypatch):
    # Companies (e.g. Fireblocks) override absolute_url to their marketing site,
    # which has no fillable form. We must ignore it and build the Greenhouse-hosted
    # URL from the job id.
    monkeypatch.setattr(greenhouse, "get_json", lambda url: {
        "jobs": [{"id": 42, "title": "Analyst", "location": {"name": "NYC"},
                  "content": "desc", "absolute_url": "https://fireblocks.com/careers"}]
    })
    out = greenhouse.fetch("fireblocks")
    assert out[0].url == "https://boards.greenhouse.io/fireblocks/jobs/42"


def test_lever_null_location_and_epoch_date(monkeypatch):
    monkeypatch.setattr(lever, "get_json", lambda url: [
        {"text": "Analyst", "categories": {"location": None},
         "descriptionPlain": "desc", "hostedUrl": "https://x/1",
         "createdAt": 1718000000000}
    ])
    out = lever.fetch("acme")
    assert len(out) == 1
    assert out[0].location == ""
    assert out[0].posted_date == "2024-06-10"  # epoch ms -> ISO date


def test_greenhouse_null_jobs_key_does_not_crash(monkeypatch):
    # {"jobs": null} (key present, explicitly null) is a real degraded-API shape,
    # not just a missing key -- .get("jobs", []) doesn't help there.
    monkeypatch.setattr(greenhouse, "get_json", lambda url: {"jobs": None, "meta": {}})
    assert greenhouse.fetch("acme") == []


def test_greenhouse_null_entry_loses_only_itself_not_the_board(monkeypatch):
    # A null/garbage entry INSIDE the jobs list (not just a null list) used to
    # AttributeError and abort the loop, losing the whole company's real jobs.
    monkeypatch.setattr(greenhouse, "get_json", lambda url: {"jobs": [
        None, "garbage",
        {"id": 7, "title": "Analyst", "location": {"name": "NYC"}, "content": "d"},
    ]})
    out = greenhouse.fetch("acme")
    assert len(out) == 1 and out[0].title == "Analyst"


def test_lever_null_entry_loses_only_itself_not_the_board(monkeypatch):
    monkeypatch.setattr(lever, "get_json", lambda url: [
        None,
        {"text": "Analyst", "categories": {"location": "NYC"}, "hostedUrl": "https://x/1"},
    ])
    out = lever.fetch("acme")
    assert len(out) == 1 and out[0].title == "Analyst"


def test_greenhouse_non_dict_location_does_not_crash(monkeypatch):
    monkeypatch.setattr(greenhouse, "get_json", lambda url: {
        "jobs": [{"title": "Analyst", "location": "NYC",  # spec violation: bare string
                  "content": "desc", "absolute_url": "https://x/1"}]
    })
    out = greenhouse.fetch("acme")
    assert len(out) == 1 and out[0].location == ""


def test_lever_infinite_created_at_does_not_crash(monkeypatch):
    # Python's json module accepts Infinity as an extension; int(float('inf'))
    # raises OverflowError, not one of the previously-caught exception types.
    monkeypatch.setattr(lever, "get_json", lambda url: [
        {"text": "Analyst", "categories": {}, "hostedUrl": "https://x/1",
         "createdAt": float("inf")}
    ])
    out = lever.fetch("acme")
    assert len(out) == 1 and out[0].posted_date == ""


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, content_type="application/json"):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.headers = {"content-type": content_type}
        self._json = json_data

    def json(self):
        return self._json


def test_workday_null_entry_in_job_postings_does_not_crash(monkeypatch):
    # A real Workday pattern: a null placeholder for a posting that failed to
    # serialize server-side.
    pages = iter([
        _FakeResponse(json_data={"jobPostings": [
            None,
            {"title": "Analyst", "locationsText": "Jersey City, NJ", "externalPath": "/job/1"},
        ]}),
        _FakeResponse(json_data={"jobPostings": []}),
    ])
    monkeypatch.setattr(workday.requests.Session, "post",
                        lambda self, url, json=None, timeout=None: next(pages))
    monkeypatch.setattr(workday.requests.Session, "get",
                        lambda self, url, timeout=None: _FakeResponse(
                            json_data={"jobPostingInfo": {"jobDescription": "desc"}}))
    out = workday.fetch("https://acme.wd5.myworkdayjobs.com/careers")
    assert len(out) == 1
    assert out[0].url.endswith("/job/1")


def test_workday_null_external_path_does_not_crash(monkeypatch):
    pages = iter([
        _FakeResponse(json_data={"jobPostings": [
            {"title": "Analyst", "locationsText": "Jersey City, NJ", "externalPath": None},
        ]}),
        _FakeResponse(json_data={"jobPostings": []}),
    ])
    monkeypatch.setattr(workday.requests.Session, "post",
                        lambda self, url, json=None, timeout=None: next(pages))
    monkeypatch.setattr(workday.requests.Session, "get",
                        lambda self, url, timeout=None: _FakeResponse(
                            json_data={"jobPostingInfo": {"jobDescription": "desc"}}))
    out = workday.fetch("https://acme.wd5.myworkdayjobs.com/careers")
    assert len(out) == 1
    assert out[0].url == "https://acme.wd5.myworkdayjobs.com/careers"


def test_workday_bot_challenge_response_is_logged_not_silently_empty(monkeypatch, capsys):
    # A 429/WAF response used to look identical to a genuinely empty board.
    monkeypatch.setattr(workday.requests.Session, "post",
                        lambda self, url, json=None, timeout=None: _FakeResponse(
                            status_code=429, content_type="text/html"))
    out = workday.fetch("https://acme.wd5.myworkdayjobs.com/careers")
    assert out == []
    assert "HTTP 429" in capsys.readouterr().out


def test_workday_url_parse():
    from jobagent.sources.workday import _URL_RE
    m = _URL_RE.match("https://citi.wd5.myworkdayjobs.com/2")
    assert m and m.groups() == ("citi", "wd5", "2")
    # en-US language segment is skipped, site captured
    m2 = _URL_RE.match("https://pwc.wd3.myworkdayjobs.com/en-US/Global_Experienced_Careers")
    assert m2.group(1) == "pwc" and m2.group(3) == "Global_Experienced_Careers"
    # non-Workday url -> no match
    assert _URL_RE.match("https://boards.greenhouse.io/x/jobs/1") is None
