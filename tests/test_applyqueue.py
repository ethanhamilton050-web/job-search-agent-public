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


def test_enqueue_never_clobbers_a_running_job():
    """Regression (found live, 2026-07-09, by an overnight adversarial audit): the
    dashboard's "+queue" button has no guard against re-queuing a listing an
    active grinder is mid-way through. Re-adding a 'running' job must be a no-op,
    not reset it back to 'queued' (which used to get silently overwritten again
    the moment the in-flight run() finished and wrote its real outcome)."""
    conn = _db()
    q.enqueue(conn, "a")
    q.mark(conn, "a", "running")
    q.enqueue(conn, "a")  # re-add while it's actively running
    assert q.pending(conn)[0]["state"] == "running"  # untouched
    # a genuinely finished job (not running) IS re-queueable, same as before
    q.mark(conn, "a", "filled")
    q.enqueue(conn, "a")
    assert q.pending(conn)[0]["state"] == "queued"


def test_claim_next_atomically_pops_and_marks_running():
    conn = _db()
    q.enqueue(conn, "a")
    q.enqueue(conn, "b")
    assert q.claim_next(conn) == "a"
    assert q.pending(conn)[0]["state"] in ("running", "queued")  # sanity: row exists
    by_id = {r["listing_id"]: r["state"] for r in q.pending(conn)}
    assert by_id == {"a": "running", "b": "queued"}
    assert q.claim_next(conn) == "b"
    assert q.claim_next(conn) is None  # queue exhausted


def test_claim_next_survives_real_concurrent_processes(tmp_path):
    """Regression (found live, 2026-07-09, by an overnight adversarial audit):
    next_queued() (read) + mark(..., 'running') (write) used to be two separate,
    unguarded statements -- two concurrent queue-run processes could both read
    the same earliest queued listing_id before either marked it running, and
    both then drive a browser against the SAME application. Proves the fix with
    GENUINE concurrency: N real threads, each its own sqlite3 connection to the
    same on-disk file, all racing to claim from a small queue at once. If the
    claim weren't atomic, the same listing_id could be returned to more than one
    thread; with the fix, every queued job is claimed by exactly one thread."""
    import threading

    db_path = tmp_path / "queue.db"
    conn0 = sqlite3.connect(db_path)
    q.ensure_table(conn0)
    listing_ids = [f"job-{i}" for i in range(10)]
    for lid in listing_ids:
        q.enqueue(conn0, lid)
    conn0.close()

    n_workers = 20
    claimed = [None] * n_workers

    def worker(i):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            claimed[i] = q.claim_next(conn)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    won = [c for c in claimed if c is not None]
    assert sorted(won) == sorted(listing_ids)  # every job claimed exactly once
    assert len(won) == len(set(won))  # no duplicates -- no two threads got the same job


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


def test_run_records_unrecognized_return_value_as_error_not_filled():
    """Regression (found live, 2026-07-09, by an overnight adversarial audit): a
    typo'd or future-caller-bug return value from apply_fn used to be silently
    recorded as 'filled' -- the same state as a genuinely completed application
    ready for review -- masking the anomaly as a false-positive success."""
    conn = _db()
    q.enqueue(conn, "a")
    q.run(conn, lambda lid: "not_a_real_state", sleep=lambda s: None, rng=lambda lo, hi: 0.0)
    assert q.pending(conn)[0]["state"] == "error"


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


def test_run_survives_a_mark_failure_and_keeps_processing_the_batch(monkeypatch):
    """Regression (found live, 2026-07-09, by an overnight adversarial audit): the
    finishing mark() call inside run()'s loop used to be unguarded, so a DB write
    hiccup there (a lock from a concurrent writer, or OneDrive syncing jobs.db
    mid-write -- this repo tree is OneDrive-synced) would propagate straight out
    of run(), abandoning every other still-queued job with no explanation. A
    walk-away unattended batch must keep going even if one job's own mark()
    fails; that job just stays 'running' until reset_stuck() reclaims it."""
    conn = _db()
    for lid in ("a", "b"):
        q.enqueue(conn, lid)

    real_mark = q.mark
    def flaky_mark(conn, lid, state, detail=None):
        if lid == "a":
            raise sqlite3.OperationalError("database is locked")
        return real_mark(conn, lid, state, detail)
    monkeypatch.setattr(q, "mark", flaky_mark)

    done = q.run(conn, lambda lid: "filled", sleep=lambda s: None, rng=lambda lo, hi: 0.0)
    assert done == 2  # both jobs were attempted despite 'a's mark() failing
    states = {r["listing_id"]: r["state"] for r in q.pending(conn)}
    assert states["a"] == "running"  # never got marked -- left for reset_stuck()
    assert states["b"] == "filled"   # unaffected by 'a's write failure


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


def test_only_one_concurrent_run_grinds_the_queue(tmp_path):
    """ISSUES G single-flight: two run() loops launched at once (a dashboard
    'Run queue now' double-click) must not BOTH grind — claim_next already stops
    a shared job, but two grinders interleaving destroys the pacing guarantee.
    Real threads, real on-disk DB, own connection each."""
    import threading

    db_path = tmp_path / "queue.db"
    conn0 = sqlite3.connect(db_path)
    q.ensure_table(conn0)
    for i in range(5):
        q.enqueue(conn0, f"job-{i}")
    conn0.close()

    done = [None, None]
    barrier = threading.Barrier(2)

    def worker(i):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            barrier.wait()
            done[i] = q.run(conn, lambda lid: "filled",
                            pace=(0, 0), sleep=lambda s: None)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(done) == [0, 5]  # one grinder did ALL the work, the other bowed out

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    states = [r["state"] for r in q.pending(conn)]
    conn.close()
    assert states == ["filled"] * 5


def test_stale_lock_is_reclaimed_but_live_lock_is_respected(tmp_path):
    db_path = tmp_path / "queue.db"
    conn = sqlite3.connect(db_path)
    q.ensure_table(conn)

    assert q.try_lock(conn, "grinder-A")
    assert not q.try_lock(conn, "grinder-B")     # live lock -> refused

    # a dead grinder's lock (old heartbeat) is taken over instead of wedging
    conn.execute("UPDATE queue_lock SET heartbeat=datetime('now', '-30 minutes')")
    conn.commit()
    assert q.try_lock(conn, "grinder-B")

    # unlock only releases the CALLER's own lock
    q.unlock(conn, "grinder-A")                  # stale owner: no-op
    assert not q.try_lock(conn, "grinder-C")
    q.unlock(conn, "grinder-B")                  # real owner: released
    assert q.try_lock(conn, "grinder-C")
    conn.close()
