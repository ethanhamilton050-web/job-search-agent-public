"""Salary-expectation math for the screening filler.

Ethan's design (2026-07-13): a saved 'salary expectation' answer shouldn't be a fixed
dollar figure (wrong on every job) but a STRATEGY — below / average / above — that maps
to a NUMBER computed from the specific job's pay range:

    range $80,000-$100,000 :  below -> 80,000   average -> 90,000   above -> 100,000

Ranges aren't in the DB `salary` column (empty for every listing) but ARE written into
~83% of job descriptions (pay-transparency laws), so we parse them from text. Pure
functions only — no DB, no Playwright — so the filler stays testable.
"""
from __future__ import annotations

import re
import statistics

STRATEGIES = ("below", "average", "above")

# A $-amount, optionally with a K suffix: "$95,000", "$95K", "95,000", "$95k".
_AMOUNT = r"\$?\s?(\d{1,3}(?:,\d{3})+|\d{2,7})(\s?[kK])?"
# Two amounts joined by a dash/'to' — the common posted-range shape.
_RANGE_RE = re.compile(_AMOUNT + r"\s*(?:-|–|—|to)\s*" + _AMOUNT)
_SINGLE_RE = re.compile(_AMOUNT)

_MIN_PLAUSIBLE = 10_000      # below this it isn't an annual salary (fees, bonuses, typos)
_MAX_PLAUSIBLE = 2_000_000


def _to_annual(digits: str, k: str | None) -> int | None:
    """'95,000' -> 95000; '95','K' -> 95000. Reject implausible-as-salary amounts."""
    try:
        v = int(digits.replace(",", ""))
    except ValueError:
        return None
    if k:                       # explicit K suffix
        v *= 1000
    elif v < 1000:              # bare "95" with no K is not an annual salary
        return None
    return v if _MIN_PLAUSIBLE <= v <= _MAX_PLAUSIBLE else None


def parse_range(text: str) -> tuple[int, int] | None:
    """Extract an annual pay range (lo, hi) from free text, or None if none is found.

    Prefers an explicit two-number range ('$80,000 - $100,000'); falls back to a single
    posted figure (returned as (v, v)). Filters out amounts too small/large to be an
    annual salary so a signing bonus or a phone-number-looking string can't masquerade."""
    if not text:
        return None
    for m in _RANGE_RE.finditer(text):
        lo = _to_annual(m.group(1), m.group(2))
        hi = _to_annual(m.group(3), m.group(4))
        if lo and hi:
            return (min(lo, hi), max(lo, hi))
    # No range — take the first plausible single figure.
    for m in _SINGLE_RE.finditer(text):
        v = _to_annual(m.group(1), m.group(2))
        if v:
            return (v, v)
    return None


def pick(lo: int, hi: int, strategy: str) -> int:
    """Map a strategy to a number within [lo, hi]: below->lo, average->midpoint, above->hi."""
    s = (strategy or "").strip().lower()
    if s == "below":
        return lo
    if s == "above":
        return hi
    return round((lo + hi) / 2)   # 'average' (and the safe default)


def estimate_range(comparable_ranges: list[tuple[int, int]]) -> tuple[int, int] | None:
    """A grounded fallback when THIS job posts no range: the median of the posted ranges
    of comparable jobs (same title family + area, gathered by the caller). Median, not
    mean, so one outlier posting can't skew it. None if there's nothing to go on — we
    then park the question rather than invent a number."""
    ranges = [r for r in comparable_ranges if r]
    if len(ranges) < 2:
        return None
    lo = int(statistics.median(r[0] for r in ranges))
    hi = int(statistics.median(r[1] for r in ranges))
    return (min(lo, hi), max(lo, hi))


if __name__ == "__main__":  # ponytail: self-check, no framework
    assert parse_range("The range for this role is $80,000 - $100,000 annually") == (80000, 100000)
    assert parse_range("$80K–$100K") == (80000, 100000)
    assert parse_range("base pay of $95,000") == (95000, 95000)
    assert parse_range("call 555-555-0100") is None       # not a salary
    assert parse_range("a $5,000 signing bonus") is None   # below plausible annual
    assert parse_range("no numbers here") is None
    assert pick(80000, 100000, "below") == 80000
    assert pick(80000, 100000, "average") == 90000
    assert pick(80000, 100000, "above") == 100000
    assert pick(80000, 100000, "") == 90000                # default = average
    assert estimate_range([(80000, 100000), (90000, 110000), (100000, 120000)]) == (90000, 110000)
    assert estimate_range([(90000, 90000)]) is None        # too few to trust
    print("salary self-check ok")
