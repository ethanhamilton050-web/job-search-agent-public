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
    """The on-disk store, or {} if missing/corrupt/wrong-shaped.

    Found live, 2026-07-09, by an overnight adversarial audit: a bare
    json.loads() with no guard meant a truncated file (a process killed
    mid-write, or a OneDrive sync-conflict copy -- this whole repo tree is
    OneDrive-synced) crashed every caller, including the dashboard's Answers
    page and a live apply run's record_unknown()/answer() calls. Treating a
    malformed file as empty is always the safe, recoverable read (worst case:
    a previously-answered question gets re-parked for you to redo once).
    """
    if not STORE.exists():
        return {}
    try:
        data = json.loads(STORE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> None:
    """Merge `data` into the on-disk store -- NEVER a wholesale replace.

    Found live, 2026-07-09: the old code overwrote the whole file with exactly
    `data`, so a save() built from a page snapshot silently erased any
    question a concurrently-running apply/queue worker had parked via
    record_unknown() after that page loaded -- the one human-review chance
    qbank exists to create, gone with no warning. Merging instead means a
    caller only ever ADDS/updates the keys it actually knows about.

    Loose-match aware: updating a question that's already stored under
    slightly different case/punctuation updates THAT entry rather than
    creating a shadow duplicate (the exact bug `record_unknown`'s own
    loose-dedup check already guards against on ITS write path).

    # ponytail: read-modify-write, not a real file lock -- if two saves land
    # within the same instant and both touch the SAME key, one still wins.
    # Fine for a single-user local tool; upgrade to a lock file / atomic
    # compare-and-swap if this ever becomes multi-writer for real.
    """
    current = load()
    norm_index = {_norm(k): k for k in current}
    for q, a in data.items():
        existing_key = norm_index.get(_norm(q))
        key = existing_key if existing_key is not None else q
        current[key] = a
        norm_index[_norm(q)] = key
    STORE.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")


def answer(question: str) -> str:
    """A remembered non-empty answer for this question, or "" if none/pending."""
    target = _norm(question)
    for q, a in load().items():
        if _norm(q) == target:
            return str(a or "").strip()
    return ""


def record_unknown(question: str) -> None:
    """Park a question the tool couldn't answer, for the human to fill once. Keeps
    any existing entry; only opens a blank slot when the question is genuinely new.

    Passes ONLY the new key to save() (not the whole loaded store) so this
    write can never stomp a concurrent, unrelated edit -- see save()'s own
    residual-race note.
    """
    q = str(question).strip()
    target = _norm(q)
    if not target:
        return
    if not any(_norm(k) == target for k in load()):
        save({q: ""})


def pending(data: dict | None = None) -> list[str]:
    """Questions still waiting on a human answer (stored blank)."""
    data = load() if data is None else data
    return [q for q, a in data.items() if not str(a or "").strip()]


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
