"""The unattended queue grinder (`main.py queue run`) must reject a known-wrong
shaped link before ever launching a browser (ISSUES.md item D) -- and must NOT
block a properly-shaped one.
"""
from types import SimpleNamespace

from jobagent import applyqueue, attempts, config, database


def _seed(db_path, url, source):
    database.init_db(db_path)
    conn = database.connect(db_path)
    try:
        database.upsert_listing(conn, {
            "id": "job-1", "title": "Analyst", "company": "Fireblocks",
            "url": url, "source": source,
        })
        applyqueue.enqueue(conn, "job-1")
        conn.commit()
    finally:
        conn.close()


def test_bad_shaped_link_never_launches_a_browser(tmp_path, monkeypatch, capsys):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    # The exact historical bug: a marketing page instead of the Greenhouse-hosted form.
    _seed(db, "https://fireblocks.com/careers", "greenhouse:fireblocks")

    import main
    from jobagent.workday import filler
    monkeypatch.setattr(filler, "fill_application",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must never launch a browser for a bad link")))

    main.cmd_queue(SimpleNamespace(action="run", ids=[]))

    conn = database.connect(db)
    try:
        row = applyqueue.pending(conn)[0]
    finally:
        conn.close()
    assert row["state"] == "needs_human"
    assert "doesn't look right" in row["detail"]


def test_well_shaped_link_is_not_blocked_by_the_link_check(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    _seed(db, "https://boards.greenhouse.io/fireblocks/jobs/999", "greenhouse:fireblocks")

    import main
    # Short-circuit one step later (the attempts cap) just to prove the link
    # check itself let this URL through rather than wrongly rejecting it.
    monkeypatch.setattr(attempts, "allowed", lambda conn, company: False)

    main.cmd_queue(SimpleNamespace(action="run", ids=[]))

    conn = database.connect(db)
    try:
        row = applyqueue.pending(conn)[0]
    finally:
        conn.close()
    assert row["state"] == "needs_human"
    assert "safety cap" in row["detail"]  # reached the NEXT gate, not stuck at the link check
