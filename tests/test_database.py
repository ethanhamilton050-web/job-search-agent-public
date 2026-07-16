"""Data-integrity regressions found live, 2026-07-09, by an overnight
adversarial audit -- each verified against a real sqlite3 connection.
"""
from jobagent import database


def _row(lid, score):
    return {"id": lid, "title": "Analyst", "company": "X", "location": "NYC",
            "remote": 0, "url": f"http://x/{lid}", "salary": "", "source": "greenhouse:x",
            "posted_date": "", "fetched_at": "2026-07-01T10:00:00", "description": "d",
            "score": score, "score_reasons": ""}


def test_ranked_listings_does_not_silently_drop_a_null_score(tmp_path, monkeypatch):
    """SQL's `NULL >= 0.0` evaluates to NULL, not true, so a listing with no
    score used to vanish from every view at ANY threshold, including
    min_score=0 -- not just ranked last, actually invisible with no error."""
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(database.config, "DB_PATH", db)
    database.init_db(db)
    conn = database.connect(db)
    database.upsert_listing(conn, _row("no-score", None))
    database.upsert_listing(conn, _row("scored", 10))
    conn.commit()

    shown_at_zero = {r["id"] for r in database.ranked_listings(conn, 0.0)}
    assert "no-score" in shown_at_zero
    assert "scored" in shown_at_zero

    # a real positive floor still correctly excludes the unscored listing
    shown_at_40 = {r["id"] for r in database.ranked_listings(conn, 40.0)}
    assert "no-score" not in shown_at_40


def test_set_status_reports_whether_a_row_actually_changed(tmp_path, monkeypatch):
    """A status update against a listing_id that's since been pruned (a scan
    can drop a stale row between page-load and a click) used to match zero
    rows with no error -- the caller had no way to know the click did nothing."""
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(database.config, "DB_PATH", db)
    database.init_db(db)
    conn = database.connect(db)
    database.upsert_listing(conn, _row("real", 50))
    conn.commit()

    assert database.set_status(conn, "real", "applied") is True
    assert database.set_status(conn, "already-deleted", "applied") is False
