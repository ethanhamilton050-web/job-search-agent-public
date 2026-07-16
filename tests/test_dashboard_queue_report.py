"""The /queue view surfaces a fill-report summary next to a job when one exists.

Points config.DB_PATH at a temp DB so the Flask app's own connect() hits our seeded
data — the report detail is a read-only decoration on the existing queue row, so this
also guards that the plain queue view (and GET /) still return 200.
"""
from jobagent import applyqueue, config, database, fillreport


def _seed(db_path):
    database.init_db(db_path)
    conn = database.connect(db_path)
    try:
        database.upsert_listing(conn, {
            "id": "job-1", "title": "Analyst", "company": "Citi",
            "url": "http://example.com/apply",
        })
        applyqueue.enqueue(conn, "job-1")
        fillreport.record(conn, "job-1", ["email"], ["salary_expectation"],
                          ["resume upload failed"])
        conn.commit()
    finally:
        conn.close()


def test_queue_shows_fill_report_detail(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)

    import dashboard
    client = dashboard.app.test_client()

    assert client.get("/").status_code == 200
    resp = client.get("/queue")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "1 flagged, 1 errors" in body


def test_queue_ok_without_report(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    database.init_db(db)
    conn = database.connect(db)
    try:
        database.upsert_listing(conn, {"id": "job-2", "title": "Clerk",
                                       "company": "ACME", "url": ""})
        applyqueue.enqueue(conn, "job-2")
        conn.commit()
    finally:
        conn.close()

    import dashboard
    resp = dashboard.app.test_client().get("/queue")
    assert resp.status_code == 200
    assert "flagged," not in resp.get_data(as_text=True)


def test_queue_run_launches_when_queued(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)  # seeds one 'queued' job

    import dashboard
    calls = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda *a, **k: calls.append(a))

    resp = dashboard.app.test_client().post("/queue/run")
    assert resp.status_code == 302
    assert len(calls) == 1  # grinder launched once


def test_updated_at_is_shown_in_local_time_not_utc(tmp_path, monkeypatch):
    """applyqueue stamps rows with SQLite datetime('now') = UTC; the queue page must
    convert to the user's local time (raw UTC looked hours off). 2026-07-15."""
    from datetime import datetime, timezone

    import dashboard
    utc = "2026-07-15 19:00:00"
    expected = (datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc)
                .astimezone().strftime("%Y-%m-%d %H:%M"))
    assert dashboard.localtime(utc) == expected
    # non-timestamps pass through untouched, never crash the page
    assert dashboard.localtime("running") == "running"
    assert dashboard.localtime(None) == ""

    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)
    body = dashboard.app.test_client().get("/queue").get_data(as_text=True)
    conn = database.connect(db)
    try:
        raw = conn.execute("SELECT updated_at FROM apply_queue").fetchone()[0]
    finally:
        conn.close()
    assert dashboard.localtime(raw) in body   # the converted stamp is what's rendered


def test_queue_run_noop_when_empty(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    database.init_db(db)  # no jobs queued

    import dashboard
    calls = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda *a, **k: calls.append(a))

    resp = dashboard.app.test_client().post("/queue/run")
    assert resp.status_code == 302
    assert calls == []  # nothing to run -> no browser launched
