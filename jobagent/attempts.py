"""Per-company attempt cap — a safety rail so testing (or an over-eager batch)
never hammers one employer.

Repeatedly auto-applying to the same company is the fastest way to get flagged at a
place you might actually want to work, so we hard-cap real automation attempts per
company and route the rest to you to do by hand.

Counts EVERY automation run (a single `apply` OR a queued one) against a company,
persisted in the DB so the cap holds across restarts. Reviewing or submitting by
hand doesn't count — only firing the filler does. Reset it with
`python main.py attempts reset` when you want to test the same company again.
"""
from __future__ import annotations

import sqlite3

CAP = 3  # max automated attempts per company. Change here if you need to.

DDL = """
CREATE TABLE IF NOT EXISTS company_attempts (
    company TEXT PRIMARY KEY,
    count   INTEGER NOT NULL DEFAULT 0,
    last    TEXT
);
"""


def _key(company: str) -> str:
    return " ".join((company or "").lower().split())


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def count(conn: sqlite3.Connection, company: str) -> int:
    ensure_table(conn)
    row = conn.execute(
        "SELECT count FROM company_attempts WHERE company=?", (_key(company),)
    ).fetchone()
    return row[0] if row else 0


def allowed(conn: sqlite3.Connection, company: str, cap: int = CAP) -> bool:
    """True if we may still auto-apply to this company. An unknown/blank company is
    never capped — we can't meaningfully say it's 'the same company' three times."""
    if not _key(company):
        return True
    return count(conn, company) < cap


def record(conn: sqlite3.Connection, company: str) -> int:
    """Count one automation attempt against a company; returns the new total.
    No-op (returns 0) for a blank company."""
    key = _key(company)
    if not key:
        return 0
    ensure_table(conn)
    conn.execute(
        "INSERT INTO company_attempts (company, count, last) VALUES (?, 1, datetime('now')) "
        "ON CONFLICT(company) DO UPDATE SET count = count + 1, last = datetime('now')",
        (key,),
    )
    conn.commit()
    return count(conn, company)


def all_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    ensure_table(conn)
    return conn.execute(
        "SELECT company, count, last FROM company_attempts ORDER BY count DESC, company"
    ).fetchall()


def reset(conn: sqlite3.Connection, company: str | None = None) -> None:
    """Clear the cap for one company, or all of them if company is None."""
    ensure_table(conn)
    if company:
        conn.execute("DELETE FROM company_attempts WHERE company=?", (_key(company),))
    else:
        conn.execute("DELETE FROM company_attempts")
    conn.commit()
