"""Greenhouse public job boards (key-free, per-company).

API: https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
You must supply the board token(s) (the company's greenhouse slug).
"""
from __future__ import annotations

from .base import get_json, make_listing, strip_html


def fetch(board: str) -> list:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    try:
        data = get_json(url)
    except Exception as exc:  # noqa: BLE001 - source failures shouldn't crash a scan
        print(f"  [greenhouse:{board}] fetch failed: {exc}")
        return []

    if not isinstance(data, dict):
        print(f"  [greenhouse:{board}] unexpected response shape; skipping")
        return []

    listings = []
    for job in (data.get("jobs") or []):  # "jobs": null (not just absent) must not crash
        if not isinstance(job, dict):
            continue  # a null/garbage entry must lose only itself, not the whole board
        loc_raw = job.get("location")
        # location is documented as an object, but don't trust a 3rd-party API to
        # never send a bare string/other shape -- found live, 2026-07-09, by an
        # overnight adversarial audit.
        loc = (loc_raw.get("name") or "") if isinstance(loc_raw, dict) else ""
        desc = strip_html(job.get("content", ""))
        # Greenhouse lets a company override absolute_url to point at their OWN
        # embedded careers page (Fireblocks does this) — which has no form we can
        # fill. Force the canonical Greenhouse-hosted job page, which always carries
        # the application form. Fall back to absolute_url only if id is missing.
        gh_id = job.get("id")
        url = (f"https://boards.greenhouse.io/{board}/jobs/{gh_id}" if gh_id
               else job.get("absolute_url", ""))
        listings.append(
            make_listing(
                title=job.get("title", ""),
                company=board,
                location=loc,
                remote="remote" in loc.lower(),
                url=url,
                source=f"greenhouse:{board}",
                posted_date=job.get("updated_at", ""),
                description=desc,
            )
        )
    return listings
