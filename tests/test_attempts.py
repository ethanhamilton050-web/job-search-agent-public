"""Per-company auto-apply safety cap."""
import sqlite3

from jobagent import attempts


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    attempts.ensure_table(conn)
    return conn


def test_cap_blocks_after_three_attempts():
    conn = _db()
    for _ in range(attempts.CAP):
        assert attempts.allowed(conn, "Citi")
        attempts.record(conn, "Citi")
    assert attempts.count(conn, "Citi") == attempts.CAP
    assert not attempts.allowed(conn, "Citi")  # the 4th attempt is refused


def test_company_key_is_normalized():
    conn = _db()
    attempts.record(conn, "Citi")
    attempts.record(conn, "  citi ")  # same company, different spacing/case
    assert attempts.count(conn, "CITI") == 2


def test_other_companies_are_independent():
    conn = _db()
    for _ in range(attempts.CAP):
        attempts.record(conn, "Citi")
    assert not attempts.allowed(conn, "Citi")
    assert attempts.allowed(conn, "Stripe")  # a different employer is unaffected


def test_blank_company_is_never_capped():
    conn = _db()
    assert attempts.allowed(conn, "")
    assert attempts.record(conn, "") == 0  # nothing recorded for an unknown company
    assert attempts.allowed(conn, "")


def test_reset_clears_the_cap():
    conn = _db()
    for _ in range(attempts.CAP):
        attempts.record(conn, "Citi")
    attempts.reset(conn, "Citi")
    assert attempts.allowed(conn, "Citi") and attempts.count(conn, "Citi") == 0
