"""Per-application fill report: what got filled, what was flagged, what errored.

The Queue & Review dashboard shows real detail ("filled 14 fields, 2 flagged for
you") instead of a bare state word. The three lists are stored as JSON blobs in one
row keyed by listing_id so the dashboard can render them without a schema change per
field. Matches applyqueue.py: a DDL string, ensure_table(conn), functions taking conn.
"""
from __future__ import annotations

import json
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS fill_reports (
    listing_id TEXT PRIMARY KEY,
    filled     TEXT,
    flagged    TEXT,
    errors     TEXT,
    updated_at TEXT
);
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def record(conn: sqlite3.Connection, listing_id: str,
           filled: list, flagged: list, errors: list) -> None:
    """Upsert the report for one application, JSON-encoding the three lists."""
    ensure_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO fill_reports "
        "(listing_id, filled, flagged, errors, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (listing_id, json.dumps(filled), json.dumps(flagged), json.dumps(errors)),
    )
    conn.commit()


def get(conn: sqlite3.Connection, listing_id: str) -> dict | None:
    """Return {"filled":[...], "flagged":[...], "errors":[...], "updated_at":...} or None."""
    ensure_table(conn)
    row = conn.execute(
        "SELECT filled, flagged, errors, updated_at FROM fill_reports "
        "WHERE listing_id=?",
        (listing_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "filled": json.loads(row["filled"]),
        "flagged": json.loads(row["flagged"]),
        "errors": json.loads(row["errors"]),
        "updated_at": row["updated_at"],
    }
