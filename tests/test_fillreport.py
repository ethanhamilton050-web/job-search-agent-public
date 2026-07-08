"""Fill report store: round-trip, overwrite-on-re-record, and None for unknown ids.

In-memory SQLite so the whole thing runs without touching the real DB.
"""
import sqlite3

from jobagent import fillreport as fr


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    fr.ensure_table(conn)
    return conn


def test_record_get_round_trip():
    conn = _db()
    filled = ["first_name", "last_name", "email"]
    flagged = ["salary_expectation", "how_did_you_hear"]
    errors = ["resume upload timed out"]
    fr.record(conn, "job-1", filled, flagged, errors)
    got = fr.get(conn, "job-1")
    assert got["filled"] == filled
    assert got["flagged"] == flagged
    assert got["errors"] == errors
    assert got["updated_at"]  # timestamp was written


def test_re_record_overwrites():
    conn = _db()
    fr.record(conn, "job-1", ["a"], ["b"], ["c"])
    fr.record(conn, "job-1", ["x", "y"], [], [])
    got = fr.get(conn, "job-1")
    assert got["filled"] == ["x", "y"]
    assert got["flagged"] == []
    assert got["errors"] == []


def test_get_unknown_returns_none():
    conn = _db()
    assert fr.get(conn, "nope") is None
