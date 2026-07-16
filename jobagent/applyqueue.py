"""Apply queue: line up N jobs, grind them one at a time with human-like pacing,
each stopping at Review — never submits. This is the headline "queue N, walk away,
come back to finished applications" loop.

State lives in the DB (`apply_queue` table) so a crash or restart resumes where it
left off. `run` takes the apply function as an argument so the loop is testable
without a browser; the real caller passes one that drives the Workday filler.
"""
from __future__ import annotations

import os
import random
import sqlite3
import time
import uuid
from typing import Callable

STATES = ["queued", "running", "filled", "needs_human", "error"]

DDL = """
CREATE TABLE IF NOT EXISTS apply_queue (
    listing_id TEXT PRIMARY KEY,
    state      TEXT DEFAULT 'queued',
    detail     TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS queue_lock (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    owner     TEXT,
    heartbeat TEXT
);
"""

# A lock whose heartbeat is older than this is a dead grinder's leftovers. The
# heartbeat only beats BETWEEN jobs, so this must exceed the longest legitimate
# single job — reset_stuck() already declares a job dead at 15 min, so by 20 a
# stale lock can only belong to a run whose job was already reclaimed.
LOCK_STALE_MINUTES = 20


def try_lock(conn: sqlite3.Connection, owner: str,
             stale_minutes: int = LOCK_STALE_MINUTES) -> bool:
    """Atomically become THE queue grinder, or return False if a live one exists.

    ISSUES G (2026-07-09 audit): claim_next() stops two concurrent grinders from
    driving the SAME job, but they'd still both run, interleaving browser
    automations with no pacing gap between them — defeating the documented
    human-like pacing guarantee. One conditional UPSERT (same pattern as
    attempts.try_record) means only one process can hold the lock; a crashed
    grinder's lock expires via the heartbeat instead of wedging the queue.
    """
    conn.executescript(DDL)
    cur = conn.execute(
        "INSERT INTO queue_lock (id, owner, heartbeat) VALUES (1, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET owner=excluded.owner, heartbeat=datetime('now') "
        "WHERE queue_lock.heartbeat < datetime('now', ?)",
        (owner, f"-{int(stale_minutes)} minutes"),
    )
    conn.commit()
    return cur.rowcount > 0


def _beat(conn: sqlite3.Connection, owner: str) -> None:
    try:
        conn.execute("UPDATE queue_lock SET heartbeat=datetime('now') "
                     "WHERE id=1 AND owner=?", (owner,))
        conn.commit()
    except Exception:  # noqa: BLE001 - a missed beat must never kill the batch
        pass


def unlock(conn: sqlite3.Connection, owner: str) -> None:
    """Release the grinder lock — only if we still own it (never stomp a rival
    that legitimately took over after our lock went stale)."""
    try:
        conn.execute("DELETE FROM queue_lock WHERE id=1 AND owner=?", (owner,))
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def enqueue(conn: sqlite3.Connection, listing_id: str) -> None:
    """Add (or reset) a job to the queue in 'queued' state.

    Never clobbers a row that's currently 'running' -- found live, 2026-07-09,
    by an overnight adversarial audit: the dashboard's "+queue" button has no
    guard against re-queuing a listing an active grinder is mid-way through;
    plain INSERT OR REPLACE used to blindly reset it to 'queued', and when the
    in-flight run() later wrote its real outcome over that same row, the
    user's intended re-queue silently vanished with no error.
    """
    ensure_table(conn)
    conn.execute(
        "INSERT INTO apply_queue (listing_id, state, detail, updated_at) "
        "VALUES (?, 'queued', NULL, datetime('now')) "
        "ON CONFLICT(listing_id) DO UPDATE SET state='queued', detail=NULL, "
        "updated_at=datetime('now') WHERE apply_queue.state != 'running'",
        (listing_id,),
    )
    conn.commit()


def mark(conn: sqlite3.Connection, listing_id: str, state: str,
         detail: str | None = None) -> None:
    if state not in STATES:
        raise ValueError(f"unknown queue state {state!r}; expected one of {STATES}")
    conn.execute(
        "UPDATE apply_queue SET state=?, detail=?, updated_at=datetime('now') "
        "WHERE listing_id=?",
        (state, detail, listing_id),
    )
    conn.commit()


def reset_stuck(conn: sqlite3.Connection, minutes: int = 15) -> int:
    """Flip jobs stuck in 'running' longer than `minutes` back to an error state.

    A grinder that crashes, is killed, or hangs on a bad link leaves its job
    'running' forever — which both locks the dashboard 'Run queue' guard AND makes
    next_queued skip it permanently. Passing minutes=0 force-resets any running job
    (the manual "unstick" button). Returns rows reset.
    """
    ensure_table(conn)
    detail = "interrupted — grinder stopped before this finished (re-add to retry)"
    if minutes <= 0:  # force-unstick everything running (the manual button)
        cur = conn.execute(
            "UPDATE apply_queue SET state='error', detail=?, updated_at=datetime('now') "
            "WHERE state='running'", (detail,))
    else:
        cur = conn.execute(
            "UPDATE apply_queue SET state='error', detail=?, updated_at=datetime('now') "
            "WHERE state='running' AND updated_at < datetime('now', ?)",
            (detail, f"-{int(minutes)} minutes"))
    conn.commit()
    return cur.rowcount


def next_queued(conn: sqlite3.Connection) -> str | None:
    ensure_table(conn)
    row = conn.execute(
        "SELECT listing_id FROM apply_queue WHERE state='queued' "
        "ORDER BY updated_at, rowid LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def claim_next(conn: sqlite3.Connection) -> str | None:
    """Atomically pop the earliest queued job and mark it 'running' in ONE step,
    so two concurrent queue-run processes can never both claim the SAME job.

    Found live, 2026-07-09, by an overnight adversarial audit: next_queued()
    (read) + mark(..., 'running') (write) used to be two separate, unguarded
    statements -- two processes (a manual "Run queue" double-click, or one
    launched while another is still starting up) could each read the same
    earliest listing_id before either marked it running, and both then call
    apply_fn on it: two independent browser automations against the one
    application, and two independent attempts-cap slots consumed for it.
    """
    ensure_table(conn)
    cur = conn.execute(
        "UPDATE apply_queue SET state='running', detail=NULL, updated_at=datetime('now') "
        "WHERE rowid = (SELECT rowid FROM apply_queue WHERE state='queued' "
        "ORDER BY updated_at, rowid LIMIT 1) "
        "RETURNING listing_id"
    )
    row = cur.fetchone()
    conn.commit()
    return row[0] if row else None


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All queue rows, newest activity first — feeds the review UI."""
    ensure_table(conn)
    return conn.execute(
        "SELECT listing_id, state, detail, updated_at FROM apply_queue "
        "ORDER BY updated_at DESC"
    ).fetchall()


def run(conn: sqlite3.Connection, apply_fn: Callable[[str], str], *,
        pace: tuple[float, float] = (90.0, 240.0),
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[float, float], float] = random.uniform) -> int:
    """Pop queued jobs and run apply_fn(listing_id) for each until the queue is empty.

    apply_fn returns either a STATES value ('filled', 'needs_human', ...) or a
    (state, detail) tuple to explain itself (e.g. why a job was skipped); anything
    unrecognized is recorded as 'error' (a caller-side anomaly, never a false
    "filled" success -- found live, 2026-07-09, that the old default could mask a
    bug as a done application ready for review). A raised exception is caught and
    recorded as 'error' so one bad job never kills the batch. Pacing (a randomized
    gap) is applied only BETWEEN jobs — human-like, and anti-ban. Returns jobs
    processed.

    Uses claim_next() (atomic pop-and-mark-running), not next_queued()+mark() as
    two separate calls, so two concurrent run() loops can never claim the same
    job. The final mark() is itself wrapped so a DB write hiccup there (e.g. a
    lock from a concurrent writer, or OneDrive syncing jobs.db mid-write -- this
    repo tree is OneDrive-synced) can't kill the rest of the batch either; the
    job just stays 'running' until reset_stuck() reclaims it.

    Single-flight: only ONE run() grinds at a time (try_lock). A second launch
    (dashboard double-click, or a manual run while one is active) prints why and
    returns 0 instead of interleaving un-paced browser automations.
    """
    ensure_table(conn)
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"
    if not try_lock(conn, owner):
        print("Another queue run is already active — not starting a second one. "
              "(A crashed run's lock frees itself after "
              f"{LOCK_STALE_MINUTES} minutes.)")
        return 0
    done = 0
    try:
        while True:
            lid = claim_next(conn)
            if lid is None:
                break
            try:
                result = apply_fn(lid)
                state, detail = result if isinstance(result, tuple) else (result, None)
                final_state = state if state in STATES else "error"
            except Exception as exc:  # noqa: BLE001 - one bad job must not kill the batch
                final_state, detail = "error", str(exc)[:300]
            try:
                mark(conn, lid, final_state, detail)
            except Exception:  # noqa: BLE001 - a write failure must not kill the batch
                pass  # left 'running'; reset_stuck() reclaims it after the stale timeout
            done += 1
            _beat(conn, owner)  # prove we're alive between jobs
            if next_queued(conn) is not None:  # pace only between jobs, not after the last
                sleep(rng(*pace))
    finally:
        unlock(conn, owner)
    return done
