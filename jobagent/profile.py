"""Parse a resume PDF/DOCX into a structured ResumeProfile.

Parsing arbitrary resumes is imperfect; this produces a best-effort draft you
review and correct. The structured JSON (profile.json) is the canonical object
the rest of the pipeline trusts — so fixing it once fixes everything downstream.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import ResumeProfile, ExperienceItem

SECTION_HINTS = {
    "summary": ["summary", "objective", "profile", "about"],
    "skills": ["skills", "technical skills", "competencies", "technologies"],
    "experience": ["experience", "employment", "work history", "professional"],
    "education": ["education", "academic"],
}
_BULLET_RE = re.compile(r"^\s*[•\-\*●▪‣·◦]\s+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(\(?\+?\d[\d\s().-]{7,}\d)")


def _find_phone(text: str) -> str | None:
    """First match with a phone-like digit count (10-15), so resume date ranges
    like '2021 - 2023' and runs of years aren't picked up as a phone number."""
    for m in _PHONE_RE.finditer(text):
        cand = m.group(0).strip()
        if 10 <= len(re.sub(r"\D", "", cand)) <= 15:
            return cand
    return None

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
# A date range like "Jan 2025 – April 2026", "Dec 2022 - Jan 2025", "2021 – 2023",
# "May 2022 – Present". The dash may be hyphen, en-dash, or em-dash.
_DATE_RANGE_RE = re.compile(
    rf"(?:{_MONTHS}\s+)?\d{{4}}\s*[–—\-]\s*"
    rf"(?:present|current|(?:{_MONTHS}\s+)?\d{{4}})",
    re.IGNORECASE,
)
# A company line ends with an org suffix even when it has no location attached.
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(inc|llc|llp|ltd|corp|co|company|firm|bank|university|college|chapter)\.?$",
    re.IGNORECASE,
)


def _is_company_header(text: str) -> bool:
    """Best-effort: is this short line naming an employer (vs. a job title or a
    bullet)? Employer lines carry a 'Company - Location' separator (resume
    bullets use number ranges like '30-50' with no spaces) or end in an org
    keyword."""
    text = text.strip()
    if not text or len(text) > 95:
        return False
    if re.search(r"\s[–—]\s", text):  # spaced en/em-dash separator
        return True
    # A spaced hyphen is ambiguous ('Title - Specialty'); only treat it as a
    # company when a 'City, ST' location or an org keyword follows.
    if re.search(r"\s-\s.*,\s*[A-Za-z]{2,}\.?$", text):
        return True
    # 'Company, City, ST' header (comma-separated location, no dash).
    if re.search(r",\s*[A-Z]{2}\.?$", text):
        return True
    return bool(_COMPANY_SUFFIX_RE.search(text.rstrip(",.")))


def _split_dates(line: str) -> tuple[str, str]:
    """Split a header line into (text_before_dates, dates). If no date range is
    present, dates is ''. A leading-tab gap before the date is trimmed."""
    m = _DATE_RANGE_RE.search(line)
    if not m:
        return line.strip(), ""
    return line[: m.start()].strip(" \t|"), line[m.start():].strip()


def _parse_experience(lines: list[str]) -> list[ExperienceItem]:
    """Parse the experience section into roles, anchoring role boundaries on
    lines that contain a date range so glyph-less bullet lines are not each
    turned into a separate empty role.

    Best-effort and tuned for the common 'Company - Location <tab> Dates' /
    'Title' / bullets layout. Known limitations (review profile.json after
    `setup`): roles with NO dates and single-line 'Title, Company, Dates'
    entries may merge title/company or attach a title as a bullet.
    """
    roles: list[ExperienceItem] = []
    cur: ExperienceItem | None = None
    pending_company = ""   # a standalone employer line seen before its title
    awaiting_title = False  # a 'Company  Dates' header needs the next line's title

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        before, dates = _split_dates(s)
        if dates:
            awaiting_title = False
            if _is_company_header(before):
                pending_company = before
                cur = ExperienceItem(company=before, title="", dates=dates)
                roles.append(cur)
                awaiting_title = True      # title is on the following line
            else:
                cur = ExperienceItem(company=pending_company, title=before, dates=dates)
                roles.append(cur)
            continue
        if awaiting_title and not _BULLET_RE.match(ln):
            cur.title = s                  # type: ignore[union-attr]
            awaiting_title = False
            continue
        if _is_company_header(s) and not _BULLET_RE.match(ln):
            pending_company = s            # employer line preceding a 'Title Dates' role
            cur = None
            continue
        # Otherwise it's a bullet (glyph-prefixed or plain).
        if cur is None:
            cur = ExperienceItem(title="(role)", company=pending_company)
            roles.append(cur)
        cur.bullets.append(_BULLET_RE.sub("", ln).strip())

    return [r for r in roles if r.title or r.company or r.bullets]


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    if suffix == ".docx":  # python-docx can't read legacy binary .doc
        import docx

        document = docx.Document(str(path))
        return "\n".join(p.text for p in document.paragraphs)
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"unsupported resume format: {suffix} "
                     "(use PDF, DOCX, TXT, or MD; convert legacy .doc to .docx first)")


def _classify(line: str) -> str | None:
    low = line.strip().lower().rstrip(":")
    if len(low) > 40:
        return None
    for section, hints in SECTION_HINTS.items():
        if any(low == h or low.startswith(h) for h in hints):
            return section
    return None


def parse_resume(path: Path) -> ResumeProfile:
    return parse_text(extract_text(path))


def parse_text(text: str) -> ResumeProfile:
    lines = [ln.rstrip() for ln in text.splitlines()]

    prof = ResumeProfile(raw_text=text)
    # Contact
    email = _EMAIL_RE.search(text)
    phone = _find_phone(text)
    if email:
        prof.contact["email"] = email.group(0)
    if phone:
        prof.contact["phone"] = phone
    # Name: first non-empty line that isn't contact info
    for ln in lines:
        if ln.strip() and not _EMAIL_RE.search(ln) and not _PHONE_RE.search(ln):
            prof.name = ln.strip()
            break

    current = None
    summary_lines: list[str] = []
    experience_lines: list[str] = []
    for ln in lines:
        section = _classify(ln)
        if section:
            current = section
            continue
        if not ln.strip():
            continue
        if current == "summary":
            summary_lines.append(ln.strip())
        elif current == "skills":
            parts = re.split(r"[,;|•]", ln)
            prof.skills.extend(s.strip() for s in parts if s.strip())
        elif current == "experience":
            experience_lines.append(ln)
        elif current == "education":
            prof.education.append(ln.strip())

    prof.experience = _parse_experience(experience_lines)
    prof.summary = " ".join(summary_lines)
    # Dedup skills, keep order
    seen = set()
    prof.skills = [s for s in prof.skills if not (s.lower() in seen or seen.add(s.lower()))]
    return prof
