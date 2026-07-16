"""One-time "grounded facts" the honesty guardrail answers screening questions from —
entered on the dashboard **My Info** page, stored locally (gitignored), merged into the
answer bank by `build_answers`.

Deliberately a SEPARATE file from `workday_answers.json`: the web form edits this, so it
must never be anywhere near the saved Workday password. Nothing here is ever guessed —
these are facts Ethan types once; the guardrail only answers when they PROVE the answer.
"""
from __future__ import annotations

import json
import re

from . import config

PATH = config.ROOT / "grounded_facts.json"
_BAK = config.ROOT / "grounded_facts.json.bak"


def load() -> dict:
    try:
        return json.loads(PATH.read_text("utf-8")) if PATH.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}  # a corrupt/unreadable file must never crash an apply run


def save(facts: dict) -> None:
    """Write the facts, keeping a .bak of the previous version (this is Ethan's typed
    data — losing it to a bad write would be worse than the extra file)."""
    if PATH.exists():
        try:
            _BAK.write_text(PATH.read_text("utf-8"), encoding="utf-8")
        except OSError:
            pass
    PATH.write_text(json.dumps(facts, indent=2), encoding="utf-8")


def _rows(many, cols) -> list:
    """Zip parallel form columns into a list of dicts, dropping fully-empty rows.
    Column names are 'prefix_key'; the stored key is the part after the first '_'."""
    cells = {c: many(c) for c in cols}
    n = max((len(v) for v in cells.values()), default=0)
    out = []
    for i in range(n):
        row = {c.split("_", 1)[1]: (cells[c][i].strip() if i < len(cells[c]) else "")
               for c in cols}
        if any(row.values()):
            out.append(row)
    return out


def parse_profile_form(single, many) -> dict:
    """Build the grounded-facts dict from a submitted **My Info** form.
    `single(name) -> str` (one value), `many(name) -> list[str]` (repeated fields).
    Empty rows are dropped; an unchecked box is False. Framework-agnostic so it unit-tests
    without Flask (the route passes request.form.get / request.form.getlist)."""
    employers = [e.strip() for e in (single("employers") or "").splitlines() if e.strip()]
    return {
        "highest_education": (single("highest_education") or "").strip(),
        "home_country": (single("home_country") or "").strip(),
        "employers_all": employers,
        "employment_history_complete": single("employment_history_complete") == "on",
        # insiders of a PUBLIC company — self OR a relative; 'amount' is the shares/%% held,
        # which these disclosures normally require.
        "insiders": _rows(many, ("insider_who", "insider_company", "insider_ticker",
                                 "insider_role", "insider_amount")),
        # relatives / close relationships who simply WORK at a company you might apply to
        # (any level) -- distinct from public-company insiders above.
        "employer_relatives": _rows(many, ("emprel_who", "emprel_company", "emprel_role")),
        "government_officials": _rows(many, ("gov_who", "gov_role", "gov_entity")),
        # FINRA/securities licenses you hold (Series 7, 63, ...) -> drives the "do you hold
        # licenses?" Yes + the "which?" checkbox list. Comma- or newline-separated.
        "finra_licenses": [x.strip() for x in re.split(r"[,\n]", single("finra_licenses") or "")
                           if x.strip()],
        # outside business activities, IP to retain, private investments
        # investments -- one per line, blank = nothing to disclose.
        "professional_disclosures": [d.strip() for d in (single("professional_disclosures") or "").splitlines()
                                     if d.strip()],
        "related_party_complete": single("related_party_complete") == "on",
    }
