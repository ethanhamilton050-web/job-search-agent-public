"""Per-attempt "black box" for an application autofill run.

Each apply run drops all its evidence into its OWN folder under output/traces/
(screenshots + a plain-English NOTES.md), and appends one line to
output/traces/INDEX.md. That's what lets Claude diagnose a stuck application
after the fact — from the folder alone, no live browser — and then correct the
bot. Before this, screenshots wrote fixed filenames and clobbered each other on
a queue run, so only the LAST application's evidence survived.

Observation-only: every function swallows its own errors. Capturing evidence
must never change or break a real apply run (same rule as filler.py's logging).

ponytail: one attempt at a time (the queue is single-flight; attended is one
process), so a single module-level `_current` is enough. If runs ever overlap,
this becomes a stack/dict keyed by attempt id.
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

from . import config

TRACES = config.ROOT / "output" / "traces"
INDEX = TRACES / "INDEX.md"

_current: dict | None = None


def start(listing_id: str | None = None, url: str | None = None,
          debug_log: str | Path | None = None) -> Path | None:
    """Begin an attempt: make its folder and remember where screenshots go.
    `debug_log` is the shared apply-debug.log — we record its size now so
    finish() can slice out just THIS attempt's lines. Returns the folder, or
    None if it couldn't be created (capture is best-effort, never fatal)."""
    global _current
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = TRACES / f"{_slug(listing_id) or 'attempt'}-{stamp}"
        folder.mkdir(parents=True, exist_ok=True)
        log = Path(debug_log) if debug_log else None
        offset = log.stat().st_size if (log and log.exists()) else 0
        _current = {"dir": folder, "listing_id": listing_id, "url": url,
                    "log": log, "log_offset": offset, "stamp": stamp,
                    "questions": [], "cur_shot": None}
        return folder
    except Exception:  # noqa: BLE001 - capture must never break the run
        _current = None
        return None


def attempt_dir() -> Path | None:
    """Where _shot()/_dump_failure() should write right now (None => legacy path)."""
    return _current["dir"] if _current else None


def note_shot(label: str) -> None:
    """Remember the most recent screenshot, so a question parked right after it
    can point at the picture it appeared on. Called from _shot()."""
    if _current is not None:
        _current["cur_shot"] = f"{label}.png"


def note_question(question: str) -> None:
    """Record a question the bot couldn't answer, tagged with the screenshot it
    was on — so reviewing a run shows each unanswered question next to its
    picture. The remember-once recall itself is qbank's job; this is just eyes."""
    if _current is None:
        return
    try:
        q = str(question).strip()
        if q:
            _current["questions"].append({"q": q, "shot": _current.get("cur_shot")})
    except Exception:  # noqa: BLE001 - capture must never break the run
        pass


def finish(state: str, errors: list[str] | None = None,
           step: str | None = None, company: str | None = None) -> None:
    """End the attempt: write NOTES.md (unless it was a clean success) and
    always append one INDEX.md line. First call wins — later calls are no-ops,
    so it's safe to call once with a real outcome and again in a `finally`."""
    global _current
    cur, _current = _current, None
    if cur is None:
        return
    try:
        errors = errors or []
        label = company or _host(cur.get("url")) or (cur.get("listing_id") or "?")
        # A run that parked questions is never "clean" — those are the whole point.
        clean = state in ("filled", "done") and not errors and not cur.get("questions")
        if not clean:
            _write_notes(cur, state, errors, step, label)
        _append_index(cur, state, errors, label)
    except Exception:  # noqa: BLE001 - logging must never break the run
        pass


# --- helpers ---------------------------------------------------------------

def _slug(s: str | None) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in str(s or "")).strip("-")


def _host(url: str | None) -> str | None:
    try:
        return urlparse(url).hostname if url else None
    except Exception:  # noqa: BLE001
        return None


def _debug_tail(cur: dict, lines: int = 30) -> str:
    log = cur.get("log")
    if not (log and log.exists()):
        return ""
    try:
        with open(log, "r", encoding="utf-8", errors="replace") as f:
            f.seek(cur.get("log_offset", 0))
            tail = f.read().splitlines()
        return "\n".join(tail[-lines:])
    except Exception:  # noqa: BLE001
        return ""


def _write_notes(cur: dict, state: str, errors: list[str], step: str | None,
                 label: str) -> None:
    shots = sorted(p.name for p in cur["dir"].glob("*.png"))
    err_block = "\n".join(f"- {e}" for e in errors) if errors else "- None captured"
    shot_block = "\n".join(f"- {s}" for s in shots) if shots else "- (none)"
    qs = cur.get("questions") or []
    q_block = "\n".join(
        f"- {it['q']}" + (f"  (see {it['shot']})" if it.get("shot") else "")
        for it in qs
    ) if qs else "- None"
    tail = _debug_tail(cur)
    tail_block = f"```\n{tail}\n```" if tail else "(no debug lines captured)"
    notes = f"""# Application attempt — {state.upper()}

- When: {time.strftime('%Y-%m-%d %H:%M:%S')}
- Job: {cur.get('url') or '(unknown)'}
- Listing id: {cur.get('listing_id') or '(none)'}
- Outcome: {state}
- Stopped at: {step or '(unspecified)'}

## Questions the bot couldn't answer (answer once on the dashboard /answers page)
{q_block}

## Errors shown on screen
{err_block}

## Screenshots (in this folder)
{shot_block}

## Last actions before it stopped (from apply-debug.log)
{tail_block}

---
*Auto-written by failpacket. To diagnose: read the screenshots + errors above,
then note the cause in ISSUES.md and fix with a guarding test (see CLAUDE.md).*
"""
    (cur["dir"] / "NOTES.md").write_text(notes, encoding="utf-8")


def _append_index(cur: dict, state: str, errors: list[str], label: str) -> None:
    TRACES.mkdir(parents=True, exist_ok=True)
    nq = len(cur.get("questions") or [])
    top = errors[0] if errors else (f"{nq} question(s) to answer" if nq else "—")
    if len(top) > 90:
        top = top[:87] + "..."
    line = (f"- {time.strftime('%Y-%m-%d %H:%M')} · {state} · {label} · "
            f"{top} · {cur['dir'].name}/\n")
    with open(INDEX, "a", encoding="utf-8") as f:
        f.write(line)
