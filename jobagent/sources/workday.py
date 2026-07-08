"""Workday public job listings (read-only).

Paste a company's Workday *careers* URL into config.sources.workday_sites
(e.g. "https://citi.wd5.myworkdayjobs.com/2"); we read its public jobs feed — the
same JSON the career page itself loads. Applying still goes through the autofiller.

We pre-filter the cheap listing feed (title + location) to your target metros and
level, then pull full descriptions only for the survivors — so a 2,000-job board
costs a handful of detail requests, not 2,000.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import requests

from .base import make_listing, strip_html
from ..scorer import location_ok, qualified

# https://{tenant}.{wd}.myworkdayjobs.com/[en-US/]{site}
_URL_RE = re.compile(
    r"https?://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", re.I)
_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Content-Type": "application/json"}


def fetch(site_url: str, targets: dict | None = None, cap: int = 300) -> list:
    m = _URL_RE.match((site_url or "").strip())
    if not m:
        print(f"  [workday] not a Workday careers URL: {site_url}")
        return []
    tenant, wd, site = m.group(1), m.group(2), m.group(3)
    root = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{root}/wday/cxs/{tenant}/{site}/jobs"
    cxs = f"{root}/wday/cxs/{tenant}/{site}"
    targets = targets or {}
    session = requests.Session()
    session.headers.update(_HDR)

    # 1) page the cheap listing feed; keep only metro + right-level jobs.
    # ponytail: caps at `cap` newest; metro jobs buried deeper are skipped
    # (raise cap, or add a location facet, if a huge employer needs it).
    keep = []
    for offset in range(0, cap, 20):
        try:
            r = session.post(api, json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                        "searchText": ""}, timeout=20)
            posts = (r.json() if "json" in r.headers.get("content-type", "") else {}).get("jobPostings") or []
        except Exception as exc:  # noqa: BLE001 - a bad board shouldn't crash the scan
            print(f"  [workday:{tenant}] fetch failed: {exc}")
            break
        if not posts:
            break
        keep += [j for j in posts
                 if location_ok(j.get("locationsText", ""), False, targets) and qualified(j.get("title", ""))]

    # 2) full description for the survivors (for scoring + the dashboard).
    # ponytail: 8 parallel detail fetches over the shared session. These were
    # serial before (one 20s-timeout request per survivor) — the scan's slowest
    # step by far. Bump max_workers if a board has hundreds of survivors.
    def _detail(j):
        desc = j.get("title", "")
        try:
            d = session.get(cxs + j.get("externalPath", ""), timeout=10).json()
            desc = strip_html(d.get("jobPostingInfo", {}).get("jobDescription", "")) or desc
        except Exception:  # noqa: BLE001
            pass
        return make_listing(
            title=j.get("title", ""), company=tenant, location=j.get("locationsText", ""),
            url=root + "/" + site + j.get("externalPath", ""),
            source=f"workday:{tenant}", posted_date=j.get("postedOn", ""), description=desc,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(_detail, keep))
    print(f"  [workday:{tenant}] {len(out)} metro matches")
    return out
