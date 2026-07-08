"""Human-friendly job/company presentation for the dashboard.

Three jobs, all display-side (nothing here changes what's scanned/scored):
  * pretty_company / company_blurb — brand-case the lowercase source slugs
    (sofi -> SoFi) and give a one-line "who are they" for the curated employers.
  * format_blocks — turn a raw pasted job description into headers / paragraphs /
    bullets the template can render safely (no raw HTML, so no XSS).
  * ai_summary — a 2-3 sentence gist from the local Ollama/Gemma box, reusing the
    same stdlib urllib call as jobagent.fieldmap. Cached in the DB by `main.py
    summarize`, so the box is off the page-load path and its downtime just means
    "no summary yet", never a broken page.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# Source adapters store the lowercase board slug as the company. Map the ones we
# actually pull to their real brand casing; unknowns fall back to Title Case.
COMPANY_NAMES = {
    "affirm": "Affirm", "brex": "Brex", "sofi": "SoFi", "robinhood": "Robinhood",
    "betterment": "Betterment", "carta": "Carta", "chime": "Chime", "gemini": "Gemini",
    "marqeta": "Marqeta", "mercury": "Mercury", "alpaca": "Alpaca",
    "fireblocks": "Fireblocks", "current": "Current", "anthropic": "Anthropic",
    "scaleai": "Scale AI", "stripe": "Stripe", "citi": "Citi", "spgi": "S&P Global",
    "travelers": "Travelers", "pnc": "PNC",
}

# One-line "who are they" so Ethan doesn't have to go look each up.
COMPANY_BLURBS = {
    "affirm": "Buy-now-pay-later fintech; point-of-sale installment loans.",
    "brex": "Corporate cards and spend management for startups and enterprises.",
    "sofi": "Digital personal-finance company and national bank (loans, investing, banking).",
    "robinhood": "Commission-free investing and trading app.",
    "betterment": "Automated investing / robo-advisory platform.",
    "carta": "Cap-table, equity, and fund-administration software for private markets.",
    "chime": "Fee-free mobile banking (neobank).",
    "gemini": "Crypto exchange and custodian founded by the Winklevoss twins.",
    "marqeta": "Modern card-issuing and payment-processing platform.",
    "mercury": "Banking and financial stack for startups.",
    "alpaca": "Developer-first stock and crypto brokerage API.",
    "fireblocks": "Institutional custody and infrastructure for digital assets.",
    "current": "Mobile banking (neobank) focused on everyday spending.",
    "anthropic": "AI safety company; maker of the Claude models.",
    "scaleai": "Data-labeling and AI infrastructure provider.",
    "stripe": "Online payments and financial infrastructure for internet businesses.",
    "citi": "Global bank; consumer, corporate, and investment banking (Citigroup).",
    "spgi": "Financial data, credit ratings, and market intelligence (S&P Global).",
    "travelers": "Property-casualty insurance carrier (Dow component).",
    "pnc": "Pittsburgh-based national bank; retail, corporate, and asset management.",
}


def pretty_company(name: str) -> str:
    """Brand-case a company slug. Known -> curated casing; short all-consonant
    unknown (kbra) -> acronym upper; else Title Case."""
    raw = (name or "").strip()
    if not raw:
        return ""
    key = raw.lower()
    if key in COMPANY_NAMES:
        return COMPANY_NAMES[key]
    if raw.isalpha() and len(raw) <= 4 and not any(v in key for v in "aeiou"):
        return raw.upper()  # ponytail: treat short vowel-less unknowns as acronyms
    return raw.title()


def company_blurb(name: str) -> str:
    """One-line description for a curated employer, or '' if we don't have one."""
    return COMPANY_BLURBS.get((name or "").strip().lower(), "")


# Fetch-time cp1252 decode errors left U+FFFD (shown as a black diamond) where a
# smart quote / dash used to be. Best-effort repair: it's almost always an
# apostrophe between letters (we're, company's) or a dash between spaces.
_FFFD_APOS = re.compile(r"(?<=\w)�(?=\w)")
_FFFD_DASH = re.compile(r"\s�\s")
_BULLET = re.compile(r"^\s*(?:[•▪●‣⁃·*\-–]|\d+[.)])\s+")


def clean_text(text: str) -> str:
    t = _FFFD_APOS.sub("'", text or "")
    t = _FFFD_DASH.sub(" — ", t)
    return t.replace("�", "")


def _is_header(line: str) -> bool:
    words = line.split()
    if line.endswith(":") and len(line) <= 60 and len(words) <= 8:
        return True
    return line.isupper() and 1 < len(words) <= 8  # short ALL-CAPS heading line


def format_blocks(text: str) -> list[tuple[str, str]]:
    """Split a raw description into ('head'|'bullet'|'para', text) blocks for the
    template to render. Source descriptions already break on newlines per
    sentence/paragraph, so one line == one block is enough."""
    blocks: list[tuple[str, str]] = []
    for line in clean_text(text).splitlines():
        s = line.strip()
        if not s:
            continue
        if _BULLET.match(s):
            blocks.append(("bullet", _BULLET.sub("", s, count=1)))
        elif _is_header(s):
            blocks.append(("head", s))
        else:
            blocks.append(("para", s))
    return blocks


def match_breakdown(reasons: str) -> dict:
    """Split the scorer's `score_reasons` string into visible buckets for the UI.

    The scorer already flattened the match into segments joined by ' | ', e.g.
    "skills: excel, python | missing: bloomberg, sql | keywords: valuation".
    Display-only: no re-scan needed, just re-reads what scan already stored."""
    matched, missing, notes = [], [], []
    for seg in (reasons or "").split(" | "):
        seg = seg.strip()
        if not seg:
            continue
        label, _, val = seg.partition(":")
        items = [v.strip() for v in val.split(",") if v.strip()]
        if label.strip().lower() in ("skills", "keywords"):
            matched.extend(items)
        elif label.strip().lower() == "missing":
            missing.extend(items)
        else:
            notes.append(seg)  # location mismatch / salary / work-auth flags
    seen: set[str] = set()
    matched = [m for m in matched if not (m in seen or seen.add(m))]
    return {"matched": matched, "missing": missing, "notes": notes}


def ai_summary(description: str, title: str = "", company: str = "", *,
               model: str = "gemma4:e4b", base: str = "http://localhost:11434",
               timeout: int = 60) -> str | None:
    """2-3 sentence gist from a local Ollama box, or None if it's unreachable/odd.

    Stdlib only (same call shape as jobagent.fieldmap.call_llm). Never raises — a
    down box just means the caller skips this listing."""
    desc = clean_text(description or "")[:3500]
    prompt = (
        "Summarize this job posting for a job seeker in 2-3 short, factual sentences: "
        "what the role does day to day and the top 2-3 requirements. No preamble, no "
        "bullet points, just the summary.\n\n"
        f"Title: {title}\nCompany: {company}\n\n{desc}"
    )
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/api/chat", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read())["message"]["content"].strip()
        return out or None
    except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError):
        return None
