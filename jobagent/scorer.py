"""Fit scoring: weighted keyword/skill overlap + title + location/auth filters.

Deliberately simple (no TF-IDF): we score one resume against one JD at a time,
where a document corpus doesn't exist. Weighted overlap is more robust here.
"""
from __future__ import annotations

import re

from .models import Listing, ResumeProfile

_AUTH_TRIGGERS = (
    "sponsorship", "visa", "security clearance", "clearance",
    "us citizen", "u.s. citizen", "citizenship required",
)

# Sub-annual pay isn't comparable to an annual floor — skip rather than misread it.
_PERIODIC = re.compile(r"\b(hour|hr|hourly|week|weekly|day|daily|month|monthly)\b|/\s*(hr|hour|wk|week|day|mo|month)", re.I)


def _term_in(term: str, text: str) -> bool:
    """Word-boundary match so short skills/keywords (c, r, go, ai) don't match
    inside words like 'company'/'are'/'growing'. + and # stay matchable (c++/c#)."""
    return re.search(rf"(?<![A-Za-z0-9+#]){re.escape(term)}(?![A-Za-z0-9+#])", text) is not None


_FOREIGN = (
    "canada", "ontario", "toronto", "quebec", "montreal", "vancouver", "british columbia",
    "united kingdom", "london", "england", "scotland", "ireland", "dublin",
    "india", "bengaluru", "bangalore", "hyderabad", "mumbai", "pune", "gurgaon",
    "australia", "sydney", "melbourne", "germany", "berlin", "munich", "france", "paris",
    "singapore", "netherlands", "amsterdam", "spain", "madrid", "barcelona", "poland",
    "brazil", "mexico", "philippines", "japan", "tokyo", "emea", "apac", "latam",
)
# Acceptable on-site metros: Chester/North NJ, NYC, Philadelphia, Pittsburgh.
_METRO = (
    "new jersey", ", nj", "chester", "morristown", "parsippany", "princeton",
    "newark", "jersey city", "hoboken", "edison", "paramus", "saddle brook", "fairfield",
    "new york", ", ny", "nyc", "manhattan", "brooklyn",
    "philadelphia", "philly", "pittsburgh", ", pa",
)


def location_ok(location: str, remote: bool, targets: dict) -> bool:
    """On-site jobs in the target metros only (Chester/NJ, NYC, Philadelphia,
    Pittsburgh). Remote and foreign jobs are excluded. The dashboard 'show all'
    toggle / `list --all` bypasses this."""
    loc = (location or "").lower()
    if remote or "remote" in loc or "anywhere" in loc:
        return False
    if not loc or any(f in loc for f in _FOREIGN):
        return False
    metros = set(_METRO)
    metros.update(t.lower().strip() for t in targets.get("locations", [])
                  if t.strip().lower() not in ("", "remote"))
    return any(m in loc for m in metros)


# Titles an early-career finance/accounting candidate isn't a fit for.
_SENIOR = ("senior", "sr.", " sr ", " sr,", "staff ", "principal", " lead ", " lead,",
           "lead ", "director", "vp ", "vp,", "vice president", "head of", "chief", "manager", " mgr")
_WRONG_DOMAIN = ("engineer", "developer", "devops", "site reliability", " sre",
                 "security analyst", "information security", "cybersecurity", "network",
                 "machine learning", " ml ", "full stack", "full-stack", "frontend",
                 "front-end", "backend", "back-end", "ios ", "android", "embedded",
                 "firmware", "account executive", "sales representative")


def qualified(title: str) -> bool:
    """Hide roles an early-career finance/accounting candidate isn't a fit for:
    senior+ levels and pure software/IT/security engineering."""
    t = f" {(title or '').lower()} "
    return not (any(s in t for s in _SENIOR) or any(d in t for d in _WRONG_DOMAIN))


def _salary_floor_flag(listing: Listing, targets: dict) -> str | None:
    """Advisory flag if the posting's stated pay is below the salary floor."""
    floor = targets.get("salary_floor") or 0
    if not floor or not listing.salary:
        return None
    if _PERIODIC.search(listing.salary):  # "$1,200/week" must not flag against a 60k floor
        return None
    nums = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?", listing.salary):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if m.group(2):
            val *= 1000
        if val >= 1000:  # ignore hourly figures / stray small numbers
            nums.append(val)
    if nums and max(nums) < floor:
        return f"below salary floor (${floor:,.0f}): {listing.salary}"
    return None


def _work_auth_flag(jd_text: str, targets: dict) -> str | None:
    """Advisory flag if you don't need sponsorship but the JD raises work-auth."""
    wa = (targets.get("work_authorization") or "").lower()
    if not wa or not any(k in wa for k in ("no sponsor", "authorized", "citizen")):
        return None
    hit = next((t for t in _AUTH_TRIGGERS if t in jd_text), None)
    return f"check work-auth: JD mentions '{hit}'" if hit else None


def score_listing(listing: Listing, profile: ResumeProfile, cfg: dict) -> tuple[float, str]:
    """Return (0-100 score, human-readable reasons)."""
    scoring = cfg["scoring"]
    targets = profile.targets or cfg.get("targets", {})

    jd_text = (listing.description + " " + listing.title).lower()
    skills = {s.lower() for s in profile.skills}
    keywords = {k.lower() for k in targets.get("keywords", [])}
    titles = [t.lower() for t in targets.get("titles", [])]

    _STOP = {"and", "of", "the", "for", "a", "an", "to", "in", "&", "-"}

    def words(text: str) -> set[str]:
        return {w for w in re.split(r"[^a-z0-9]+", text.lower())
                if len(w) > 2 and w not in _STOP}

    # 1. Skill overlap (word-boundary match; reward hits with diminishing returns —
    #    4+ matched skills counts as a full match so niche skills don't dilute).
    skill_hits = {s for s in skills if _term_in(s, jd_text)}
    skill_score = min(1.0, len(skill_hits) / 4)

    # 2. Title match: best word-overlap ratio between any target title and the
    #    listing title (so "Equity Research Analyst" ~ "Finance and Equity Analyst").
    lt = listing.title.lower()
    lt_words = words(listing.title)
    title_score = 0.0
    for t in titles:
        tw = words(t)
        if tw:
            title_score = max(title_score, len(tw & lt_words) / len(tw))

    # 3. Keyword match (diminishing returns, 4+ = full).
    kw_hits = {k for k in keywords if _term_in(k, jd_text)}
    kw_score = min(1.0, len(kw_hits) / 4)

    raw = (
        scoring["weight_skill_overlap"] * skill_score
        + scoring["weight_title_match"] * title_score
        + scoring["weight_keyword_match"] * kw_score
    )

    # Seniority penalty: this is an early-to-mid search, so over-level roles
    # (Director/Head/VP/Principal) shouldn't outrank analyst/associate roles.
    seniority_mult = 1.0
    if scoring.get("penalize_seniority", True):
        markers = {
            "chief": 0.30, "vp": 0.35, "vice president": 0.35, "head of": 0.40,
            "director": 0.45, "principal": 0.55, "manager": 0.75, "staff": 0.80,
            "lead": 0.82, "senior": 0.88, "sr": 0.88,
        }
        # Whole-word match only, so "Stafford"/"Managerial"/"ahead of" don't
        # trip the penalty (a bare substring check wrongly demoted those).
        for marker, mult in markers.items():
            if re.search(rf"\b{re.escape(marker)}\b", lt):
                seniority_mult = min(seniority_mult, mult)

    score = round(raw * seniority_mult * 100, 1)

    # Hard filters expressed as reasons (don't zero the score, just warn)
    reasons = []
    if skill_hits:
        reasons.append(f"skills: {', '.join(sorted(skill_hits))}")
    missing = sorted(skills - skill_hits)[:5]
    if missing:
        reasons.append(f"missing: {', '.join(missing)}")
    if kw_hits:
        reasons.append(f"keywords: {', '.join(sorted(kw_hits))}")

    if not targets.get("remote_ok", True) and listing.remote:
        reasons.append("note: remote role")
    locs = [loc.lower() for loc in targets.get("locations", [])]
    if locs and listing.location and not listing.remote:
        if not any(loc in listing.location.lower() for loc in locs):
            reasons.append(f"location mismatch: {listing.location}")

    salary_note = _salary_floor_flag(listing, targets)
    if salary_note:
        reasons.append(salary_note)
    auth_note = _work_auth_flag(jd_text, targets)
    if auth_note:
        reasons.append(auth_note)

    return score, " | ".join(reasons)
