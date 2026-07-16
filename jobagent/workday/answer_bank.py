"""The answer bank: every field you retype on each Workday application.

Most of it is derived from your resume profile (name, contact, work history,
education). The rest — EEO/voluntary self-ID, "how did you hear about us",
common screening questions — you set once in workday_answers.json and reuse.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import config, facts
from ..models import ResumeProfile

ANSWERS_PATH = config.ROOT / "workday_answers.json"


def _load_profile() -> ResumeProfile:
    if not config.PROFILE_PATH.exists():
        raise FileNotFoundError("profile.json missing — run `python main.py setup`")
    return ResumeProfile.from_dict(json.loads(config.PROFILE_PATH.read_text("utf-8")))


def _overlay() -> dict:
    if ANSWERS_PATH.exists():
        return json.loads(ANSWERS_PATH.read_text("utf-8"))
    return {}


def build_answers() -> dict:
    """Merge resume profile + workday_answers.json into one flat answer bank."""
    prof = _load_profile()
    overlay = _overlay()

    first, _, last = (prof.name or "").partition(" ")
    answers: dict = {
        "first_name": first,
        "last_name": last.strip(),  # leave blank for mononyms, don't duplicate first name
        "email": prof.contact.get("email", ""),
        "phone": prof.contact.get("phone", ""),
        "resume_file": str(_resume_path() or ""),
        "work_authorized": True,
        "needs_sponsorship": False,
        "experience": [
            {
                # profile stores "Company – City, ST"; split so Workday's separate
                # Company and Location fields each get the right half.
                "company": re.split(r"\s[–—-]\s", e.company, maxsplit=1)[0].strip(),
                "location": (re.split(r"\s[–—-]\s", e.company, maxsplit=1) + [""])[1].strip(),
                "title": e.title,
                "dates": e.dates,
                # one bullet per line (resume style) — Workday's Role Description is a
                # textarea and keeps the newlines, so it reads like the resume not a wall.
                "summary": "\n".join(f"• {b}" for b in e.bullets)[:1800],
            }
            for e in prof.experience
        ],
        "education": prof.education,
        "skills": prof.skills,
    }
    # Overlay wins (lets you override anything + add Workday-only fields:
    # eeo, gender, race, veteran_status, disability_status, how_did_you_hear,
    # linkedin_url, github_url, salary_expectation, screening_answers{...}).
    answers.update(overlay)
    # Grounded facts from the dashboard "My Info" page win last (that form is the UI source
    # of truth). Skip blank strings so an empty field can't clobber a value set elsewhere.
    answers.update({k: v for k, v in facts.load().items() if v != ""})
    return answers


# Highest degree, checked high -> low, so the FIRST match is the highest level present.
_DEGREE_LEVELS = [
    (r"ph\.?\s?d|doctor(al|ate)?|d\.?phil|\bj\.?d\b|\bm\.?d\b", "Doctorate (PhD)"),
    (r"\bm\.?b\.?a\b", "MBA"),
    (r"master|\bm\.?\s?s\b|\bm\.?\s?a\b|m\.?eng|m\.?sc|graduate\s+degree", "Master's Degree"),
    (r"bachelor|\bb\.?\s?s\b|\bb\.?\s?a\b|b\.?eng|b\.?sc|undergraduate", "Bachelor's Degree"),
    (r"associate|\ba\.?\s?s\b|\ba\.?\s?a\b", "Associate's Degree"),
    (r"high\s+school|diploma|\bged\b", "High School Diploma"),
]


# A degree keyword next to one of these is NOT yet earned — a candidate / student, coursework
# "toward" it, an expected/anticipated date, ABD ("all but dissertation"). Claiming an
# in-progress degree as completed is résumé fraud, so a qualified mention is skipped and we
# fall to the highest degree actually COMPLETED; if none is provable, return "" (park for the
# human). Errs toward parking: an unrelated nearby qualifier can suppress a real degree, which
# is the safe direction (asks the human rather than asserting a credential).
_INPROGRESS_RE = re.compile(
    r"candidate|pursuing|in[\s-]?progress|expected|anticipated|coursework|towards?\b"
    r"|\bstudent\b|\babd\b|currently\s+enrolled|to\s+be\s+completed|ongoing", re.I)


def _highest_education(education) -> str:
    """Derive the highest COMPLETED education level from the resume's education line(s). A degree
    named as in-progress (candidate / pursuing / expected / student / ABD / coursework) is NOT
    counted — we never assert a degree that isn't finished; if none is provably complete, return
    "" so the human fills it in."""
    text = (" ".join(education) if isinstance(education, list) else str(education or "")).lower()
    for pat, level in _DEGREE_LEVELS:
        for m in re.finditer(pat, text):
            window = text[max(0, m.start() - 30): m.end() + 30]
            if not _INPROGRESS_RE.search(window):
                return level          # highest degree with a COMPLETED (non-in-progress) mention
    return ""


def _profile_text(prof) -> str:
    """Every free-text field on the resume worth scanning for credentials, as one blob:
    raw text, summary, skills, education, and experience bullets (licenses often sit in a
    skills/certifications line, not just the raw text)."""
    parts = [prof.raw_text or "", prof.summary or "", " ".join(prof.skills or []),
             " ".join(prof.education or [])]
    for e in prof.experience or []:
        parts.append(" ".join(e.bullets or []))
    return "\n".join(parts)


def _licenses_from_text(text: str) -> list[str]:
    """FINRA/securities + insurance licenses named in resume text, for PRE-FILLING My Info.
    Series exams (incl. shorthand lists like "Series 6 & 63"), the SIE, and an insurance
    producer license. The insurance match requires a 'producer'/'license' anchor so a stray
    'health insurance' benefits mention can't masquerade as a credential. \b guards the Series
    number so a 4-digit year ("Series 2020 report") can't match. This is only a starting value
    the human reviews and Saves — nothing here ever ticks a checkbox unconfirmed."""
    t = text or ""
    found: list[str] = []
    for m in re.finditer(r"\bseries\s+(\d{1,3}\b(?:\s*(?:,|&|/|and)\s*\d{1,3}\b)*)", t, re.I):
        found += [f"Series {n}" for n in re.findall(r"\b\d{1,3}\b", m.group(1))]
    if re.search(r"\bSIE\b|securities\s+industry\s+essentials", t, re.I):
        found.append("SIE")
    ins = re.search(r"(?:life|health|accident|property|casualty)[\w &/,-]{0,30}?"
                    r"insurance\s+(?:producer|licen[sc]e)|insurance\s+producer", t, re.I)
    if ins:
        found.append(" ".join(ins.group(0).split()))  # keep the resume's own casing
    seen, out = set(), []
    for x in found:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def profile_defaults() -> dict:
    """Starting values for the dashboard My Info form, derived from the resume PROFILE so a
    user is never asked to re-type facts their resume already has: home country, highest
    education level, a first-pass employer list, and any FINRA/insurance licenses named on
    the resume (they confirm/extend it). Best-effort — returns {} if there's no profile yet."""
    try:
        prof = _load_profile()
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return {
        "home_country": _overlay().get("country", "United States of America"),
        "highest_education": _highest_education(prof.education),
        "employers_all": [re.split(r"\s[–—-]\s", e.company, maxsplit=1)[0].strip()
                          for e in prof.experience if e.company],
        "finra_licenses": _licenses_from_text(_profile_text(prof)),
    }


def _resume_path() -> Path | None:
    """The PDF/DOCX in input/ to upload to Workday, or None. (Workday's uploader
    wants a real document, so .txt/.md that `setup` accepts are skipped here.)"""
    if not config.INPUT_DIR.exists():
        return None
    files = sorted(config.INPUT_DIR.glob("*.pdf")) + sorted(config.INPUT_DIR.glob("*.docx"))
    return files[0] if files else None


def save_template() -> Path:
    """Write workday_answers.example.json showing all overridable fields."""
    template = {
        "_comment": "Copy to workday_answers.json. These override/extend what is "
                    "derived from your resume. Leave 'decline' for self-ID if preferred.",
        "_comment_creds": "ONE reusable Workday login. The filler signs in with these on "
                          "every employer, or creates the account if it doesn't exist yet. "
                          "Use a real inbox you can verify from. Defaults to your resume email.",
        "workday_email": "",
        "workday_password": "",
        "address_line1": "",
        "city": "",
        "state": "",
        "postal_code": "",
        "country": "United States of America",
        "linkedin_url": "",
        "github_url": "",
        "how_did_you_hear": "Company website",
        "_comment_salary": "Workday salary fields usually require a NUMBER — plain "
                           "integer, no $, commas, or text (e.g. 80000). Set your ask.",
        "salary_expectation": 80000,
        "work_authorized": True,
        "needs_sponsorship": False,
        "_comment_screening_facts": "Hard facts the honesty guardrail uses to auto-answer "
                                    "universal screening questions. Leave a field OUT (or empty) "
                                    "and that question is ALWAYS routed to you instead of guessed.",
        "is_over_18": True,
        "is_veteran": False,
        "_comment_grounded": "Grounded facts the honesty guardrail uses to AUTO-ANSWER more "
                             "screening questions. EASIEST: enter these on the dashboard's "
                             "'My Info' page instead of editing JSON. Nothing is guessed -- a "
                             "fact you leave out still routes to you.",
        "highest_education": "",
        "_comment_home_country": "Home-address / country-of-residence questions. Falls back "
                                 "to 'country' above if left blank.",
        "home_country": "",
        "_comment_employers": "Your COMPLETE work history (fuller than the resume, which shows "
                              "only some jobs). 'ever worked at X' answers Yes if X is listed; "
                              "answers No only when 'employment_history_complete' is true.",
        "employers_all": [],
        "employment_history_complete": False,
        "_comment_insiders": "'insider' = an officer, director, or 10%+ owner of a PUBLICLY "
                             "TRADED company -- YOU or a relative. 'amount' is the shares/%% "
                             "held (these disclosures normally require it). who = 'self' or "
                             "the relation (e.g. 'mother'). With 'related_party_complete' true, "
                             "an EMPTY list auto-answers insider questions 'No'; any entry parks "
                             "it so you add the specifics.",
        "insiders": [
            {"who": "", "company": "", "ticker": "", "role": "", "amount": ""}
        ],
        "_comment_gov": "You or a relative who is a government/public official (a PEP check). "
                        "who = 'self' or the relation.",
        "government_officials": [
            {"who": "", "role": "", "entity": ""}
        ],
        "related_party_complete": False,
        "_comment_education": "Optional: structured education so the filler can fill "
                              "school/degree/field/GPA. If omitted it best-effort fills the "
                              "school name only and you finish the rest.",
        "education_structured": [
            {"school": "", "degree": "", "field": "", "gpa": "", "end_year": ""}
        ],
        "voluntary_self_id": {
            "gender": "decline",
            "race_ethnicity": "decline",
            "veteran_status": "decline",
            "disability_status": "decline"
        },
        "screening_answers": {
            "willing_to_relocate": "Yes",
            "earliest_start_date": "2 weeks",
            "require_sponsorship": "No"
        }
    }
    path = config.ROOT / "workday_answers.example.json"
    path.write_text(json.dumps(template, indent=2), encoding="utf-8")
    return path
