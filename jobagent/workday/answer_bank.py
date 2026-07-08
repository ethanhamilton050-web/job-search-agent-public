"""The answer bank: every field you retype on each Workday application.

Most of it is derived from your resume profile (name, contact, work history,
education). The rest — EEO/voluntary self-ID, "how did you hear about us",
common screening questions — you set once in workday_answers.json and reuse.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import config
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
    return answers


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
