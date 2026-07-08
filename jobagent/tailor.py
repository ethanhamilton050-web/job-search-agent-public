"""Resume tailoring guardrails.

The AI rewrite happens in a Claude Code session (your Max sub). This module
does the deterministic safety work around it:

  build_brief()  -> a self-contained prompt you paste to Claude Code.
  validate()     -> verifies the returned draft changed VERBIAGE ONLY:
                    no number/date/metric added, changed, or dropped;
                    no new employers/titles/skills/claims;
                    then shows a word-level diff for human approval.

Design note: the validator's hard guarantee is that numeric/entity FACTS are
preserved. It cannot detect a sentence that keeps the same number but attaches
it to a false claim (e.g. "team of 8" -> "team of 8 engineers"). That is what
the human diff-approval gate is for. Read the diff.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass

from .models import ResumeProfile

# --- Number / date canonicalization -----------------------------------------

_MULTIPLIER_WORDS = {
    "thousand": 1_000,
    "k": 1_000,
    "million": 1_000_000,
    "m": 1_000_000,
    "mm": 1_000_000,
    "billion": 1_000_000_000,
    "b": 1_000_000_000,
}

# Matches: $1.2M, 1,200,000, 1.2 million, 35%, 35 percent, 3x, 2021, '21
# Word suffixes may be space-separated; single-letter units must be attached and
# word-bounded so "b" in "budget" is never mistaken for "billion".
_NUM_RE = re.compile(
    r"""
    (?P<currency>\$)?\s*
    (?P<num>\d[\d,]*(?:\.\d+)?)
    (?:
        \s*(?P<word>percent|thousand|million|billion)\b
      | (?P<sym>%)
      | (?P<unit>mm|k|m|b|x)\b
    )?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_APOS_YEAR_RE = re.compile(r"'(\d{2})\b")

# Spelled-out quantities the digit-based _NUM_RE can't see ("three years",
# "a decade", "doubled"). NOT canonicalized — surfaced as a warning so the human
# diff gate catches a changed word-number. Excludes million/percent/thousand/
# billion, which _NUM_RE already handles when attached to a digit.
_QTY_WORD_RE = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|dozen|half|quarter|"
    r"double|doubled|triple|tripled|quadruple|quadrupled|"
    r"decade|decades|century|centuries)\b",
    re.IGNORECASE,
)


def _quantity_words(text: str) -> Counter:
    return Counter(w.lower() for w in _QTY_WORD_RE.findall(text))


@dataclass(frozen=True)
class Fact:
    """A canonicalized numeric fact: kind in {value, pct, mult, year}.

    `currency` distinguishes a dollar amount from a bare number so dropping the
    `$` (e.g. "$1.2M budget" -> "1.2M budget") is caught as a changed fact.
    """

    kind: str
    value: float
    currency: bool = False

    def __str__(self) -> str:  # for readable diffs
        n = self._fmt(self.value)
        if self.kind == "pct":
            return f"{n}%"
        if self.kind == "mult":
            return f"{n}x"
        if self.kind == "year":
            return f"{int(self.value)}"
        return f"{'$' if self.currency else ''}{n}"

    @staticmethod
    def _fmt(v: float) -> str:
        # Comma-grouped, full precision, never scientific notation, so two
        # different dropped numbers never render identically (e.g. 1.2e+06).
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def extract_facts(text: str) -> Counter:
    """Return a multiset of canonicalized numeric facts found in text."""
    facts: Counter = Counter()

    apos_spans: list[tuple[int, int]] = []
    for m in _APOS_YEAR_RE.finditer(text):
        yy = int(m.group(1))
        year = 2000 + yy if yy < 50 else 1900 + yy
        facts[Fact("year", float(year))] += 1
        apos_spans.append(m.span(1))  # the two digits we just counted as a year

    for m in _NUM_RE.finditer(text):
        ns, ne = m.span("num")
        # Skip ONLY the digits _APOS_YEAR_RE already counted as a year (the "21"
        # in "'21"), so we don't double-count. A bare "'90s" that _APOS_YEAR_RE
        # does NOT match (no word boundary before the 's') must still be counted
        # here, or a changed decade ('90s -> '80s) would slip through unnoticed.
        if any(s <= ns and ne <= e for s, e in apos_spans):
            continue
        raw = m.group("num").replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        word = (m.group("word") or "").lower()
        sym = m.group("sym")
        unit = (m.group("unit") or "").lower()
        currency = bool(m.group("currency"))
        # A trailing "dollars"/"USD" marks a currency amount too, so "$2M" and
        # "2 million dollars" canonicalize to the same fact.
        if not currency and re.match(
            r"\s*(?:dollars?|usd|bucks)\b", text[m.end():m.end() + 12], re.IGNORECASE
        ):
            currency = True

        if sym == "%" or word == "percent":
            facts[Fact("pct", val)] += 1
        elif unit == "x":
            facts[Fact("mult", val)] += 1
        elif unit in _MULTIPLIER_WORDS or word in _MULTIPLIER_WORDS:
            facts[Fact("value", val * _MULTIPLIER_WORDS[unit or word],
                       currency=currency)] += 1
        elif (
            not currency
            and not (word or sym or unit)
            and val.is_integer()
            and 1900 <= val <= 2099
        ):
            facts[Fact("year", val)] += 1
        else:
            facts[Fact("value", val, currency=currency)] += 1
    return facts


# --- Entity / skill extraction ----------------------------------------------

def _proper_nouns(text: str) -> list[str]:
    """Crude proper-noun grab: capitalized tokens, original case preserved."""
    return [w for w in re.findall(r"\b[A-Z][A-Za-z0-9&.\-]+\b", text) if len(w) > 1]


# --- Validation -------------------------------------------------------------

@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    warnings: list[str]
    diff: str

    def report(self) -> str:
        lines = []
        lines.append("PASS" if self.ok else "FAIL")
        for e in self.errors:
            lines.append(f"  [ERROR] {e}")
        for w in self.warnings:
            lines.append(f"  [warn]  {w}")
        return "\n".join(lines)


def _fact_diff(original: Counter, tailored: Counter) -> tuple[list, list]:
    added = list((tailored - original).elements())
    dropped = list((original - tailored).elements())
    return added, dropped


def validate(original_text: str, tailored_text: str,
             profile: ResumeProfile | None = None) -> ValidationResult:
    """Verify the tailored text is a verbiage-only edit of the original."""
    errors: list[str] = []
    warnings: list[str] = []

    orig_facts = extract_facts(original_text)
    new_facts = extract_facts(tailored_text)
    added, dropped = _fact_diff(orig_facts, new_facts)
    if added:
        errors.append(
            "New/changed numbers introduced (not in your resume): "
            + ", ".join(str(f) for f in added)
        )
    if dropped:
        errors.append(
            "Numbers from your resume were dropped or altered: "
            + ", ".join(str(f) for f in dropped)
        )

    # Entity lock: no brand-new proper nouns (employers, schools, products).
    orig_nouns = {w.lower() for w in _proper_nouns(original_text)}
    stop = {"the", "a", "an", "i", "we", "led", "managed", "built", "and"}
    # Tokenize multiword skills ("Amazon Web Services") so each word is recognized
    # against the per-word proper-noun output — else they spuriously warn.
    known = {tok.lower()
             for s in (profile.skills if (profile and profile.skills) else [])
             for tok in s.split()}
    new_nouns: list[str] = []
    seen: set[str] = set()
    for w in _proper_nouns(tailored_text):
        wl = w.lower()
        if wl in orig_nouns or wl in stop or wl in known or wl in seen:
            continue
        seen.add(wl)
        new_nouns.append(w)
    if new_nouns:
        warnings.append(
            "New capitalized terms appeared — confirm these aren't invented "
            "employers/tools/claims: " + ", ".join(new_nouns)
        )

    # Spelled-out quantities aren't auto-locked like digits — warn so the human
    # verifies a changed duration/quantity in word form ("three years"->"five").
    qty_added, qty_dropped = _fact_diff(
        _quantity_words(original_text), _quantity_words(tailored_text)
    )
    if qty_added or qty_dropped:
        parts = []
        if qty_added:
            parts.append("added: " + ", ".join(sorted(set(qty_added))))
        if qty_dropped:
            parts.append("removed: " + ", ".join(sorted(set(qty_dropped))))
        warnings.append(
            "Spelled-out quantity words changed (NOT auto-checked as numbers — "
            "verify the duration/quantity is unchanged): " + "; ".join(parts)
        )

    diff = "\n".join(
        difflib.unified_diff(
            original_text.splitlines(),
            tailored_text.splitlines(),
            fromfile="master_resume",
            tofile="tailored",
            lineterm="",
        )
    )

    return ValidationResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        diff=diff,
    )


# --- Brief construction -----------------------------------------------------

BRIEF_RULES = """\
RULES (must follow exactly):
- Edit VERBIAGE ONLY. Rephrase for clarity and to mirror the job's keywords.
- Do NOT add, remove, or change any number, percentage, dollar amount, date,
  or duration. Every metric in the master must appear unchanged.
- Do NOT invent or alter employers, job titles, schools, certifications, or
  tools. Use only what is in the master resume.
- Do NOT add skills or claims that aren't already supported by the master.
- Keep every quantified bullet; do not drop accomplishments.
- Return ONLY the tailored resume text, same structure as the master.
"""


def build_brief(profile: ResumeProfile, listing) -> str:
    """Build the self-contained prompt to paste into a Claude Code session."""
    skills = ", ".join(profile.skills)
    parts = [
        "Tailor my resume for the job below. " + BRIEF_RULES,
        "\n=== TARGET JOB ===",
        f"Title: {listing.title}",
        f"Company: {listing.company}",
        f"Location: {listing.location}",
        "Description:",
        (listing.description or "").strip()[:6000],
        "\n=== MY MASTER RESUME ===",
        f"Name: {profile.name}",
        f"Summary: {profile.summary}",
        f"Skills: {skills}",
        "Experience:",
    ]
    for exp in profile.experience:
        parts.append(f"- {exp.title}, {exp.company} ({exp.dates})")
        for b in (exp.bullets or []):
            parts.append(f"  * {b}")
    if profile.education:
        parts.append("Education: " + "; ".join(profile.education))
    return "\n".join(parts)
