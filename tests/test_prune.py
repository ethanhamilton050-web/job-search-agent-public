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


def test_prune_case_insensitive_source_matching(tmp_path, monkeypatch):
    """Regression (found live, 2026-07-09, by an overnight adversarial audit): a
    Workday listing's stored `source` preserves the tenant's ORIGINAL case from
    the configured URL, but main.py's _configured_source_keys derives its
    comparison set via urlparse().hostname, which Python's urllib ALWAYS
    lowercases. A config.json URL with any uppercase tenant letter (e.g. pasted
    from a browser as "Citi...") made every stale row for that employer look
    "not configured" on a mere transient fetch failure -- real data loss from
    a network blip, exactly what this function is supposed to prevent."""
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    database.init_db(db)
    conn = database.connect(db)

    OLD = "2026-07-01T10:00:00"
    cutoff = "2026-07-02T12:00:00"
    # stored with ORIGINAL case (as workday.py's regex capture preserves it)
    database.upsert_listing(conn, _row("citi-job", "workday:Citi", OLD))
    conn.commit()

    # caller's sets, as main.py's _configured_source_keys really produces them
    # (urlparse().hostname is always lowercase) -- Citi's board timed out this
    # scan (not in succeeded), but IS still configured.
    n = database.prune_stale_listings(
        conn, cutoff, succeeded=set(), attempted={"workday:citi"})
    conn.commit()

    assert n == 0  # must survive -- the board is still configured, just failed this scan
    remaining = {r[0] for r in conn.execute("SELECT id FROM listings")}
    assert "citi-job" in remaining


def test_configured_source_keys_lowercases_workday_tenant():
    """Audit U7: the prune-safety chain was only tested by hand-feeding the derived
    key set. This tests the deriver itself — _configured_source_keys must emit the
    Workday tenant LOWERCASED (matching the case-insensitive prune compare), or an
    uppercase config URL silently reopens the 2026-07-09 data-loss bug."""
    import main
    cfg = {"sources": {
        "greenhouse_boards": ["Acme"],
        "lever_boards": ["Beta"],
        "workday_sites": ["https://Citi.wd5.myworkdayjobs.com/CitiCareers"],
    }}
    keys = main._configured_source_keys(cfg)
    assert "workday:citi" in keys        # lowercased -> matches the stored-source compare
    assert "greenhouse:Acme" in keys     # gh/lever board tokens match their stored source as-is
    assert "lever:Beta" in keys
