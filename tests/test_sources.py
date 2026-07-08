"""Source adapters must survive null/odd fields the APIs really send."""
from jobagent.sources import greenhouse, lever


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


def test_workday_url_parse():
    from jobagent.sources.workday import _URL_RE
    m = _URL_RE.match("https://citi.wd5.myworkdayjobs.com/2")
    assert m and m.groups() == ("citi", "wd5", "2")
    # en-US language segment is skipped, site captured
    m2 = _URL_RE.match("https://pwc.wd3.myworkdayjobs.com/en-US/Global_Experienced_Careers")
    assert m2.group(1) == "pwc" and m2.group(3) == "Global_Experienced_Careers"
    # non-Workday url -> no match
    assert _URL_RE.match("https://boards.greenhouse.io/x/jobs/1") is None
