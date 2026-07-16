"""The Apply button must tell the truth when it can't open a browser.

Found live 2026-07-13 (host session): clicking Apply on a company already at the
safety cap showed the "Opening a browser…" page but nothing launched -- the real
"cap reached" refusal went only to the detached background process, so it looked
like a launch that silently vanished. The route now pre-checks the cap and shows
an honest "capped -- nothing opened" page with a one-click Reset, and never fires
the (invisible) subprocess in that case.
"""
from jobagent import attempts, config, database


def _seed(db_path):
    database.init_db(db_path)
    conn = database.connect(db_path)
    try:
        database.upsert_listing(conn, {
            "id": "job-1", "title": "Analyst", "company": "Acme Bank",
            "url": "http://x/1", "score": 50, "score_reasons": "",
        })
        conn.commit()
    finally:
        conn.close()


def test_uncapped_apply_shows_opening_and_launches(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)

    import dashboard
    launched = {}
    monkeypatch.setattr(dashboard.subprocess, "Popen",
                        lambda *a, **k: launched.setdefault("yes", (a, k)))

    resp = dashboard.app.test_client().post("/apply/job-1")
    assert resp.status_code == 200
    assert b"Opening the application" in resp.data
    assert "yes" in launched  # the autofiller subprocess WAS fired


def test_capped_apply_says_capped_and_never_launches(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)
    # Max out the cap for this company.
    conn = database.connect(db)
    try:
        for _ in range(attempts.CAP):
            attempts.record(conn, "Acme Bank")
    finally:
        conn.close()

    import dashboard
    fired = {}
    monkeypatch.setattr(dashboard.subprocess, "Popen",
                        lambda *a, **k: fired.setdefault("yes", True))

    resp = dashboard.app.test_client().post("/apply/job-1")
    assert resp.status_code == 200
    assert b"safety cap" in resp.data
    assert b"Opening the application" not in resp.data
    assert "yes" not in fired  # crucially: no invisible subprocess was launched


def test_reset_button_clears_the_cap(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db)
    conn = database.connect(db)
    try:
        for _ in range(attempts.CAP):
            attempts.record(conn, "Acme Bank")
        assert attempts.allowed(conn, "Acme Bank") is False
    finally:
        conn.close()

    import dashboard
    resp = dashboard.app.test_client().post("/attempts/reset/job-1")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"

    conn = database.connect(db)
    try:
        assert attempts.allowed(conn, "Acme Bank") is True  # cap cleared
    finally:
        conn.close()
