"""Apply queue: state round-trip + the run loop (pacing, ordering, error isolation).

Uses an in-memory SQLite DB and a fake apply_fn/sleep so the whole loop runs
without a browser.
"""
import sqlite3

import pytest

from jobagent import applyqueue as q


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    q.ensure_table(conn)
    return conn


def test_enqueue_next_mark_round_trip():
    conn = _db()
    q.enqueue(conn, "a")
    q.enqueue(conn, "b")
    assert q.next_queued(conn) == "a"  # FIFO by insertion time
    q.mark(conn, "a", "filled")
    assert q.next_queued(conn) == "b"
    q.mark(conn, "b", "needs_human", "2 questions flagged")
    assert q.next_queued(conn) is None
    states = {r["listing_id"]: r["state"] for r in q.pending(conn)}
    assert states == {"a": "filled", "b": "needs_human"}


def test_mark_rejects_unknown_state():
    conn = _db()
    q.enqueue(conn, "a")
    with pytest.raises(ValueError):
        q.mark(conn, "a", "submitted")  # 'submitted' is deliberately not a state


def test_run_processes_all_paces_between_and_records_outcomes():
    conn = _db()
    for lid in ("a", "b", "c"):
        q.enqueue(conn, lid)
    processed, sleeps = [], []

    def apply_fn(lid):
        processed.append(lid)
        return "needs_human" if lid == "b" else "filled"

    done = q.run(conn, apply_fn, sleep=sleeps.append, rng=lambda lo, hi: 1.0)
    assert done == 3
    assert processed == ["a", "b", "c"]           # order preserved
    assert len(sleeps) == 2                        # paced BETWEEN jobs, not after the last
    states = {r["listing_id"]: r["state"] for r in q.pending(conn)}
    assert states == {"a": "filled", "b": "needs_human", "c": "filled"}


def test_run_accepts_state_detail_tuple():
    conn = _db()
    q.enqueue(conn, "a")
    # apply_fn may return (state, detail) to explain a skip (e.g. company cap hit).
    q.run(conn, lambda lid: ("needs_human", "safety cap reached"),
          sleep=lambda s: None, rng=lambda lo, hi: 0.0)
    row = q.pending(conn)[0]
    assert row["state"] == "needs_human" and row["detail"] == "safety cap reached"


def test_run_isolates_a_failing_job():
    conn = _db()
    for lid in ("a", "b"):
        q.enqueue(conn, lid)

    def apply_fn(lid):
        if lid == "a":
            raise RuntimeError("browser crashed")
        return "filled"

    done = q.run(conn, apply_fn, sleep=lambda s: None, rng=lambda lo, hi: 0.0)
    assert done == 2  # the crash on 'a' did not kill the batch
    states = {r["listing_id"]: r["state"] for r in q.pending(conn)}
    assert states["a"] == "error" and states["b"] == "filled"


def test_reset_stuck_frees_stale_running_only():
    conn = _db()
    q.enqueue(conn, "old"); q.mark(conn, "old", "running")
    q.enqueue(conn, "fresh"); q.mark(conn, "fresh", "running")
    # backdate 'old' to 30 min ago; 'fresh' stays now
    conn.execute("UPDATE apply_queue SET updated_at=datetime('now','-30 minutes') "
                 "WHERE listing_id='old'")
    conn.commit()

    freed = q.reset_stuck(conn, minutes=15)
    states = {r["listing_id"]: r["state"] for r in q.pending(conn)}
    assert freed == 1
    assert states["old"] == "error"      # stale -> freed
    assert states["fresh"] == "running"  # a live run isn't clobbered


def test_reset_stuck_zero_minutes_force_frees_all_running():
    conn = _db()
    q.enqueue(conn, "a"); q.mark(conn, "a", "running")
    freed = q.reset_stuck(conn, minutes=0)  # the manual "unstick now" button
    assert freed == 1
    assert q.pending(conn)[0]["state"] == "error"
