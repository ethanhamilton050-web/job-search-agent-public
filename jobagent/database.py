"""SQLite persistence for listings and the application pipeline."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    company     TEXT,
    location    TEXT,
    remote      INTEGER,
    url         TEXT,
    salary      TEXT,
    source      TEXT,
    posted_date TEXT,
    fetched_at  TEXT,
    description TEXT,
    score       REAL,
    score_reasons TEXT,
    summary     TEXT          -- cached AI gist (nullable; filled by `main.py summarize`)
);

CREATE INDEX IF NOT EXISTS idx_listings_score ON listings(score DESC);

CREATE TABLE IF NOT EXISTS applications (
    listing_id  TEXT PRIMARY KEY REFERENCES listings(id),
    status      TEXT DEFAULT 'found',   -- found|tailored|applied|interview|offer|rejected
    doc_path    TEXT,
    follow_up   TEXT,
    notes       TEXT,
    updated_at  TEXT
);
"""

STATUSES = ["found", "tailored", "applied", "interview", "offer", "rejected"]


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    from . import applyqueue  # local import avoids an import cycle
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        # Migrate DBs created before `summary` existed (SQLite has no ADD COLUMN IF NOT EXISTS).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
        if "summary" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN summary TEXT")
        applyqueue.ensure_table(conn)  # so ranked_listings can always LEFT JOIN it
        conn.commit()
    finally:
        conn.close()


def set_summary(conn: sqlite3.Connection, listing_id: str, summary: str) -> None:
    conn.execute("UPDATE listings SET summary=? WHERE id=?", (summary, listing_id))


def upsert_listing(conn: sqlite3.Connection, row: dict) -> None:
    cols = [
        "id", "title", "company", "location", "remote", "url", "salary",
        "source", "posted_date", "fetched_at", "description",
        "score", "score_reasons",
    ]
    values = {c: row.get(c) for c in cols}
    values["remote"] = int(bool(values.get("remote")))
    placeholders = ",".join(f":{c}" for c in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    conn.execute(
        f"INSERT INTO listings ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        values,
    )
    # Ensure an application row exists in 'found' state.
    conn.execute(
        "INSERT OR IGNORE INTO applications (listing_id, status, updated_at) "
        "VALUES (?, 'found', datetime('now'))",
        (row["id"],),
    )


def prune_stale_listings(conn: sqlite3.Connection, cutoff: str,
                         succeeded: set[str], attempted: set[str]) -> int:
    """Delete listings not refreshed since `cutoff` (an ISO timestamp), keeping the
    DB to the live inventory instead of a graveyard of every job ever seen.

    Because listing ids hash the URL, a fixed/changed URL leaves the old row behind;
    dropped sources also linger forever. This removes both — but SAFELY:
      - a stale row is deleted only if its source demonstrably worked this scan
        (`succeeded`) or is no longer configured (`source not in attempted`);
      - a configured source that returned nothing (a transient network failure) keeps
        its rows, so a blip can't wipe real jobs;
      - anything the user engaged with (application status != 'found', or queued) is
        always kept.
    Returns the number of listings deleted.
    """
    has_queue = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='apply_queue'"
    ).fetchone() is not None
    protected = {r[0] for r in conn.execute(
        "SELECT listing_id FROM applications WHERE status != 'found'")}
    if has_queue:
        protected |= {r[0] for r in conn.execute("SELECT listing_id FROM apply_queue")}

    # Case-insensitive: found live, 2026-07-09, by an overnight adversarial
    # audit. A Workday listing's stored `source` preserves the tenant's
    # ORIGINAL case from the configured URL (main.py's regex capture doesn't
    # lowercase it), but `_configured_source_keys` derives its comparison set
    # via urlparse().hostname, which Python's urllib ALWAYS lowercases. A
    # config.json URL with any uppercase letter in the tenant (e.g. pasted
    # from a browser as "Citi...") made every one of that employer's stale
    # rows look "not configured" on a mere transient fetch failure -- the
    # exact real data loss this function's own docstring says must never
    # happen from a network blip.
    succeeded_ci = {s.lower() for s in succeeded}
    attempted_ci = {s.lower() for s in attempted}
    victims = [
        lid for lid, source in conn.execute(
            "SELECT id, source FROM listings WHERE fetched_at < ?", (cutoff,))
        if lid not in protected and (
            (source or "").lower() in succeeded_ci
            or (source or "").lower() not in attempted_ci
        )
    ]
    conn.executemany("DELETE FROM listings WHERE id=?", [(v,) for v in victims])
    # Drop application rows orphaned by the delete (all were status='found').
    conn.execute("DELETE FROM applications WHERE listing_id NOT IN (SELECT id FROM listings)")
    return len(victims)


def set_status(conn: sqlite3.Connection, listing_id: str, status: str,
               doc_path: str | None = None, follow_up: str | None = None,
               notes: str | None = None) -> bool:
    """Update an application's status. Returns whether a row actually changed --
    found live, 2026-07-09, by an overnight adversarial audit: an UPDATE against
    a listing_id that's since been pruned (a scan can drop a stale 'found' row
    between the dashboard rendering and a click on it) matches zero rows with
    no error, and the old code never checked -- the caller redirected as if it
    worked, with no indication the click did nothing."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
    fields = ["status=?", "updated_at=datetime('now')"]
    params: list = [status]
    if doc_path is not None:
        fields.append("doc_path=?"); params.append(doc_path)
    if follow_up is not None:
        fields.append("follow_up=?"); params.append(follow_up)
    if notes is not None:
        fields.append("notes=?"); params.append(notes)
    params.append(listing_id)
    cur = conn.execute(f"UPDATE applications SET {','.join(fields)} WHERE listing_id=?", params)
    return cur.rowcount > 0


def get_listing(conn: sqlite3.Connection, listing_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()


def ranked_listings(conn: sqlite3.Connection, min_score: float = 0.0) -> list[sqlite3.Row]:
    # COALESCE(l.score, 0): a NULL score used to vanish from every view at ANY
    # threshold, including min_score=0 -- SQL's "NULL >= 0.0" is NULL, never
    # true, so the row is silently excluded by the WHERE clause, not just
    # ranked last. Found live, 2026-07-09, by an overnight adversarial audit:
    # today's only writer (cmd_scan) always sets a real score, but there's no
    # NOT NULL/DEFAULT on the column, so any future insert path that omits
    # 'score' would make that listing invisible everywhere, forever, with no
    # error. Treating a missing score as 0 keeps it visible at the "show
    # everything" threshold while still correctly excluding it once a real
    # positive floor is set.
    return conn.execute(
        "SELECT l.*, a.status, a.doc_path, a.follow_up, a.notes, q.state AS queue_state "
        "FROM listings l JOIN applications a ON a.listing_id = l.id "
        "LEFT JOIN apply_queue q ON q.listing_id = l.id "
        "WHERE COALESCE(l.score, 0) >= ? ORDER BY l.score DESC",
        (min_score,),
    ).fetchall()
