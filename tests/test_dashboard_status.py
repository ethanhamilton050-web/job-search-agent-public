"""The /status route must tell the dashboard when a click did nothing (the
listing was pruned between page-load and the click) instead of redirecting
as if it worked -- found live, 2026-07-09, by an overnight adversarial audit.
"""
from jobagent import config, database


def _seed(db_path):
    database.init_db(db_path)
    conn = database.connect(db_path)
    try:
        database.upsert_listing(conn, {
            "id": "job-1", "title": "Analyst", "company": "Acme", "url": "http://x/1",
            "score": 50, "score_reasons": "",
        })
        conn.commit()
    finally:
        conn.close()


def test_status_update_on_real_listing_redirects_clean(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)

    import dashboard
    resp = dashboard.app.test_client().post("/status/job-1/applied")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"


def test_status_update_on_missing_listing_flags_stale(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)

    import dashboard
    resp = dashboard.app.test_client().post("/status/does-not-exist/applied")
    assert resp.status_code == 302
    assert "stale=1" in resp.headers["Location"]

    page = dashboard.app.test_client().get(resp.headers["Location"])
    assert b"didn't do anything" in page.data
