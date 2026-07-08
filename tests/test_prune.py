"""scan's prune keeps the DB to the live inventory without wiping real jobs.

Listing ids hash the URL, so a fixed URL or dropped source leaves stale rows behind.
prune_stale_listings must delete those — but only when it's SAFE (source worked, or is
no longer configured), never for a source that merely failed a scan, and never for a
job the user has applied to or queued.
"""
from jobagent import applyqueue, config, database


def _row(lid, source, fetched_at):
    return {"id": lid, "title": "Analyst", "company": "X", "location": "NYC",
            "remote": 0, "url": f"http://x/{lid}", "salary": "", "source": source,
            "posted_date": "", "fetched_at": fetched_at, "description": "d",
            "score": 50, "score_reasons": ""}


def test_prune_is_safe_and_thorough(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    database.init_db(db)
    conn = database.connect(db)

    OLD, NEW = "2026-07-01T10:00:00", "2026-07-02T13:00:00"
    cutoff = "2026-07-02T12:00:00"

    database.upsert_listing(conn, _row("fresh", "greenhouse:acme", NEW))          # current scan -> keep
    database.upsert_listing(conn, _row("dead", "greenhouse:acme", OLD))           # source worked, gone -> prune
    database.upsert_listing(conn, _row("failed", "workday:citi", OLD))            # configured but 0 results -> keep
    database.upsert_listing(conn, _row("dropped", "greenhouse:stripe", OLD))      # source no longer configured -> prune
    database.upsert_listing(conn, _row("applied", "greenhouse:acme", OLD))        # stale+prunable BUT applied -> keep
    database.upsert_listing(conn, _row("queued", "greenhouse:acme", OLD))         # stale+prunable BUT queued -> keep
    database.set_status(conn, "applied", "applied")
    applyqueue.enqueue(conn, "queued")
    conn.commit()

    n = database.prune_stale_listings(
        conn, cutoff,
        succeeded={"greenhouse:acme"},                  # workday:citi failed (not here)
        attempted={"greenhouse:acme", "workday:citi"},  # stripe not configured
    )
    conn.commit()

    remaining = {r[0] for r in conn.execute("SELECT id FROM listings")}
    assert n == 2
    assert remaining == {"fresh", "failed", "applied", "queued"}
    # applications for deleted listings are cleaned up; kept ones survive
    apps = {r[0] for r in conn.execute("SELECT listing_id FROM applications")}
    assert "dead" not in apps and "dropped" not in apps
    assert {"applied", "queued", "fresh", "failed"} <= apps
