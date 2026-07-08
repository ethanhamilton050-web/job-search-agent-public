"""Lever public postings (key-free, per-company).

API: https://api.lever.co/v0/postings/{board}?mode=json
"""
from __future__ import annotations

import datetime

from .base import get_json, make_listing, strip_html


def _to_iso_date(created_at) -> str:
    """Lever `createdAt` is epoch milliseconds; render YYYY-MM-DD to match
    greenhouse's ISO date. Returns '' if absent/unparseable."""
    if not created_at:
        return ""
    try:
        return datetime.datetime.fromtimestamp(
            int(created_at) / 1000, tz=datetime.timezone.utc
        ).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def fetch(board: str) -> list:
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    try:
        data = get_json(url)
    except Exception as exc:  # noqa: BLE001
        print(f"  [lever:{board}] fetch failed: {exc}")
        return []

    if not isinstance(data, list):
        print(f"  [lever:{board}] unexpected response shape; skipping")
        return []

    listings = []
    for job in data:
        cats = job.get("categories", {}) or {}
        loc = cats.get("location") or ""  # null location -> "" (matches the idiom below)
        desc = strip_html(job.get("descriptionPlain") or job.get("description", ""))
        listings.append(
            make_listing(
                title=job.get("text", ""),
                company=board,
                location=loc,
                remote="remote" in (loc or "").lower(),
                url=job.get("hostedUrl", ""),
                source=f"lever:{board}",
                posted_date=_to_iso_date(job.get("createdAt")),
                description=desc,
            )
        )
    return listings
