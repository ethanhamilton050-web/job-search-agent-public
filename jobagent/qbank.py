"""Remembered answers to application screening questions.

A flat {question_text: answer} store the human fills ONCE and the fillers reuse.
Two jobs, both honesty-first:

  - A question the tool couldn't answer is *parked* here blank ("") so the
    dashboard can surface it. A blank slot is a request FOR the human, never an
    answer — only a non-empty stored value is ever auto-filled.
  - Matching is loose (case/space/trailing-?-insensitive) so "Are you 18 or
    older?" and "are you 18 or older" are the same remembered question.

The universal hard facts (age, work authorization, sponsorship, veteran) are still
owned by guardrail.py + workday_answers.json; this store is for everything else —
the employer-specific questions that used to fall through to the terminal.
"""
from __future__ import annotations

import json

from . import config

STORE = config.ROOT / "screening_answers.json"


def _norm(q: str) -> str:
    return " ".join(str(q).lower().split()).rstrip("?:. ").strip()


def load() -> dict:
    if STORE.exists():
        return json.loads(STORE.read_text("utf-8"))
    return {}


def save(data: dict) -> None:
    STORE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def answer(question: str) -> str:
    """A remembered non-empty answer for this question, or "" if none/pending."""
    target = _norm(question)
    for q, a in load().items():
        if _norm(q) == target:
            return (a or "").strip()
    return ""


def record_unknown(question: str) -> None:
    """Park a question the tool couldn't answer, for the human to fill once. Keeps
    any existing entry; only opens a blank slot when the question is genuinely new."""
    q = str(question).strip()
    target = _norm(q)
    if not target:
        return
    data = load()
    if not any(_norm(k) == target for k in data):
        data[q] = ""
        save(data)


def pending(data: dict | None = None) -> list[str]:
    """Questions still waiting on a human answer (stored blank)."""
    data = load() if data is None else data
    return [q for q, a in data.items() if not (a or "").strip()]


if __name__ == "__main__":  # ponytail: self-check, no framework
    import tempfile
    from pathlib import Path

    STORE = Path(tempfile.mkdtemp()) / "s.json"
    assert answer("Are you 18 or older?") == ""
    record_unknown("Are you currently employed by PNC?")
    assert pending() == ["Are you currently employed by PNC?"]
    # loose match: re-recording the same question with different case/space/? is a no-op
    record_unknown("are you currently employed by pnc")
    assert len(load()) == 1
    save({"Are you currently employed by PNC?": "No"})
    assert answer("are you currently employed by PNC") == "No"  # remembered + loose match
    assert pending() == []
    print("qbank self-check ok")
