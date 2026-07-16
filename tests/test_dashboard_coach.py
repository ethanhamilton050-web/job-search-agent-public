"""Tests for the /coach dashboard routes: suggestion display and the
approve-and-apply-to-profile.json flow.
"""
import json

from jobagent import config, database


def _seed(db_path):
    database.init_db(db_path)
    conn = database.connect(db_path)
    try:
        database.upsert_listing(conn, {
            "id": "job-1", "title": "Analyst", "company": "Acme",
            "description": "Looking for someone with forecasting experience.",
            "score": 60, "score_reasons": "skills: excel | missing: forecasting",
        })
        conn.commit()
    finally:
        conn.close()


def _write_profile(path):
    path.write_text(json.dumps({
        "name": "Test User",
        "experience": [{"company": "Acme", "title": "Analyst", "dates": "2020-2023",
                         "bullets": ["Built financial models in Excel."]}],
    }), encoding="utf-8")


def test_coach_page_shows_job_not_found(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    database.init_db(db)

    import dashboard
    resp = dashboard.app.test_client().get("/coach/does-not-exist")
    assert resp.status_code == 200
    assert "Job not found" in resp.get_data(as_text=True)


def test_coach_page_without_profile(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)
    monkeypatch.setattr(config, "PROFILE_PATH", tmp_path / "profile.json")

    import dashboard
    resp = dashboard.app.test_client().get("/coach/job-1")
    assert resp.status_code == 200
    assert "run `python main.py setup`" in resp.get_data(as_text=True)


def test_coach_apply_writes_bullet_and_backs_up_original(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)
    profile_path = tmp_path / "profile.json"
    monkeypatch.setattr(config, "PROFILE_PATH", profile_path)
    _write_profile(profile_path)
    original_contents = profile_path.read_text("utf-8")

    import dashboard
    client = dashboard.app.test_client()
    resp = client.post("/coach/job-1/apply", data={
        "company": "Acme", "title": "Analyst",
        "original": "Built financial models in Excel.",
        "suggested": "Built financial models in Excel for quarterly forecasting.",
    })
    assert resp.status_code == 302
    assert "applied=1" in resp.headers["Location"]

    updated = json.loads(profile_path.read_text("utf-8"))
    assert updated["experience"][0]["bullets"] == [
        "Built financial models in Excel for quarterly forecasting."]

    backup_path = tmp_path / "profile.json.bak"
    assert backup_path.exists()
    assert backup_path.read_text("utf-8") == original_contents


def test_coach_apply_rejects_a_fabricated_rewrite_even_though_bullet_matches(
        tmp_path, monkeypatch):
    # A POST is just form data -- nothing guarantees "suggested" is still the
    # actual validated rewrite the page showed. The apply route must re-run
    # the fact-lock check itself, not just trust the request.
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)
    profile_path = tmp_path / "profile.json"
    monkeypatch.setattr(config, "PROFILE_PATH", profile_path)
    _write_profile(profile_path)
    original_contents = profile_path.read_text("utf-8")

    import dashboard
    resp = dashboard.app.test_client().post("/coach/job-1/apply", data={
        "company": "Acme", "title": "Analyst",
        "original": "Built financial models in Excel.",
        # invents a number that wasn't in the original -- exactly what tailor.validate exists to catch
        "suggested": "Built financial models in Excel, saving the team 40% in time.",
    })
    assert resp.status_code == 302
    assert "applied=0" in resp.headers["Location"]
    assert profile_path.read_text("utf-8") == original_contents
    assert not (tmp_path / "profile.json.bak").exists()


def test_coach_apply_no_match_leaves_profile_untouched(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)
    profile_path = tmp_path / "profile.json"
    monkeypatch.setattr(config, "PROFILE_PATH", profile_path)
    _write_profile(profile_path)
    original_contents = profile_path.read_text("utf-8")

    import dashboard
    resp = dashboard.app.test_client().post("/coach/job-1/apply", data={
        "company": "Acme", "title": "Analyst",
        "original": "This bullet does not exist in the resume.",
        "suggested": "Something else.",
    })
    assert resp.status_code == 302
    assert "applied=0" in resp.headers["Location"]
    assert profile_path.read_text("utf-8") == original_contents
    assert not (tmp_path / "profile.json.bak").exists()
