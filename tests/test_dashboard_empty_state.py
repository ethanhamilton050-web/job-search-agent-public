"""The job list must never be a silent blank table (2026-07-15): when filters hide
everything (or the DB is fresh), the page says so and points at 'show all locations' /
`scan` — same class as the capped-Apply fix (silent nothing looks broken).

config.load_config is monkeypatched so the test never depends on Ethan's real
config.json filters/thresholds.
"""
from jobagent import config, database

NEUTRAL_CFG = {"scoring": {"min_score_to_show": 0}, "targets": {}}


def test_empty_db_shows_guidance_not_a_bare_table(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "load_config", lambda: NEUTRAL_CFG)
    database.init_db(db)

    import dashboard
    body = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert "Nothing to show" in body
    assert "main.py scan" in body


def test_a_visible_listing_hides_the_empty_message(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "load_config", lambda: NEUTRAL_CFG)
    database.init_db(db)
    conn = database.connect(db)
    try:
        database.upsert_listing(conn, {
            "id": "job-1", "title": "Analyst", "company": "Acme", "url": "http://x/1",
            "location": "New York, NY, US", "score": 50, "score_reasons": "",
        })
        conn.commit()
    finally:
        conn.close()

    import dashboard
    body = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert "Analyst" in body
    assert "Nothing to show" not in body
