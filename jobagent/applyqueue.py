"""Apply queue: line up N jobs, grind them one at a time with human-like pacing,
each stopping at Review — never submits. This is the headline "queue N, walk away,
come back to finished applications" loop.

State lives in the DB (`apply_queue` table) so a crash or restart resumes where it
left off. `run` takes the apply function as an argument so the loop is testable
without a browser; the real caller passes one that drives the Workday filler.
"""
from __future__ import annotations

import random
import sqlite3
import time
from typing import Callable

STATES = ["queued", "running", "filled", "needs_human", "error"]

DDL = """
CREATE TABLE IF NOT EXISTS apply_queue (
    listing_id TEXT PRIMARY KEY,
    state      TEXT DEFAULT 'queued',
    detail     TEXT,
    updated_at TEXT
);
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def enqueue(conn: sqlite3.Connection, listing_id: str) -> None:
    """Add (or reset) a job to the queue in 'queued' state."""
    ensure_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO apply_queue (listing_id, state, detail, updated_at) "
        "VALUES (?, 'queued', NULL, datetime('now'))",
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
        "ORDER BY updated_at LIMIT 1"
    ).fetchone()
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
    unrecognized is recorded as 'filled'. A raised exception is caught and recorded
    as 'error' so one bad job never kills the batch. Pacing (a randomized gap) is
    applied only BETWEEN jobs — human-like, and anti-ban. Returns jobs processed.
    """
    ensure_table(conn)
    done = 0
    while True:
        lid = next_queued(conn)
        if lid is None:
            break
        mark(conn, lid, "running")
        try:
            result = apply_fn(lid)
            state, detail = result if isinstance(result, tuple) else (result, None)
            mark(conn, lid, state if state in STATES else "filled", detail)
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the batch
            mark(conn, lid, "error", str(exc)[:300])
        done += 1
        if next_queued(conn) is not None:  # pace only between jobs, not after the last
            sleep(rng(*pace))
    return done
