"""Per-ATS URL shape validation (ISSUES.md item D).

Catches an obviously wrong/malformed application link before a browser launch
is wasted on it in the UNATTENDED queue run -- with NO live network request,
so no risk of Workday/Greenhouse bot-protection 403ing a bare reachability
check and false-positive-skipping a real job (that's why a naive "ping the
URL first" check was rejected).

Deliberately lenient: this only rejects a KNOWN-wrong domain/shape, never
guesses at an exact ID format. Lever's posting-id length isn't locked down
here on purpose -- tests/test_apply_routing.py's own fixture uses a short
placeholder ("abc-def"), so a strict UUID-only check would have false-
rejected a URL this codebase already treats as valid.

Ceiling: this can't catch a job that was live at scan time and has since
been taken down (404/closed posting) -- that's a genuinely time-based problem
a shape check can't see. Add a real (careful, source-aware) reachability
check if dead-but-well-shaped links turn out to still be common.
"""
from __future__ import annotations

import re

# Greenhouse hosts real, currently-live postings on BOTH domains -- found by
# testing against the real 1058-listing DB, not just hand-picked examples: a
# real Affirm posting used job-boards.greenhouse.io (their newer platform) and
# a narrower regex would have wrongly rejected it as "known-wrong shape."
_GREENHOUSE_RE = re.compile(r"^https://(?:boards|job-boards)\.greenhouse\.io/[^/]+/jobs/\d+", re.I)
_LEVER_RE = re.compile(r"^https://jobs\.lever\.co/[^/]+/[^/?#]+", re.I)
_ASHBY_RE = re.compile(r"^https://jobs\.ashbyhq\.com/[^/]+/[^/?#]+", re.I)
# A Workday APPLICATION link must have a /job/<...> path. The old check reused
# the source adapter's careers-SITE pattern, which also matched a bare
# "https://tenant.wd5.myworkdayjobs.com/Site" -- exactly what a listing with a
# missing externalPath degrades to, and Workday is the one ATS where the queue
# launches a real unattended browser (ISSUES G, 2026-07-09 audit finding).
_WORKDAY_JOB_RE = re.compile(
    r"^https?://[^.]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[^/?#]+/job/[^/?#]+",
    re.I)

_SOURCE_PATTERNS = {
    "greenhouse": _GREENHOUSE_RE,
    "lever": _LEVER_RE,
    "ashby": _ASHBY_RE,
    "workday": _WORKDAY_JOB_RE,
}


def looks_like_application_url(url: str, source: str) -> bool:
    """True if `url` matches the known-good shape for its `source` prefix
    (e.g. "greenhouse:sofi", "lever:carta", "workday:citi"). An unrecognized
    source (or one we have no pattern for) is allowed through unchecked --
    this only rejects a shape we KNOW is wrong, never an unfamiliar-but-
    possibly-fine one.
    """
    url = (url or "").strip()
    if not url:
        return False
    kind = (source or "").split(":", 1)[0].lower()
    pattern = _SOURCE_PATTERNS.get(kind)
    return bool(pattern.match(url)) if pattern else True
