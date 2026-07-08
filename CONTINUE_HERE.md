# Continue Here — session resume point

*Living handoff doc. Overwrite it each session; don't let it accumulate. Last updated
2026-07-02 (session 5 — project audit & cleanup).*

**Read order:** `CLAUDE.md` (auto-loaded — rules + doc map) → this file →
`ISSUES.md` (open bugs) → `PRODUCT_PLAN.md` (master plan) → `AFTER_LAUNCH.md`
(deferred backlog).

---

## Where we are right now

**Session 5 was a cleanup/audit pass (no feature work). 136 tests pass.**

What changed:
1. **`CLAUDE.md` created** — the single rules file every future session auto-loads:
   doc map (one owner per topic), the anti-regression protocol (run pytest before/
   after; fixed log in ISSUES.md; never edit filler.py blind), secrets rules, the
   CAP=3 gotcha.
2. **`ISSUES.md` restructured** — open issues (A–E) up front; all fixed items are
   now one-line log entries with their guarding test. That log is the anti-
   regression memory: if a listed test fails, an old fix regressed.
3. **`HOST_SETUP.md` deleted** — its unique bits (Setup.bat, doctor) folded into
   README's Setup section. One less overlapping instruction file.
4. **Stale references fixed** — PRODUCT_PLAN no longer points at the long-gone
   `MORNING.md`.
5. **Git cleanup** — the auto-backup watcher had been committing
   `.browser-profile.bak/` (≈500 browser-cache files, ~40 MB); now gitignored and
   untracked. Dead `_fill_nth()` removed from filler.py (superseded by
   `_fill_card`).
6. **Code audit verdict:** the Python itself is lean (~6,650 non-test lines, no
   speculative abstractions). The confusion was doc sprawl + no CLAUDE.md, not
   code bloat. Two deferred cleanups are parked in ISSUES-adjacent notes below.

**Suggested manual cleanup for Ethan (his data, his call — nothing deleted):**
- `output/traces/` holds ~200 MB of old debug zips/screenshots from 2026-06-29 —
  safe to delete once the PNC retest passes.
- `.browser-profile.bak/` (~40 MB) is an old copy of the browser profile — safe to
  delete if the live `.browser-profile/` works.

## Next steps (unchanged priorities, from session 4)

1. **PNC live retest** — confirm the new `_fill_source` ("How Did You Hear About
   Us?") gets past the page that blocked PNC. See ISSUES.md → Open A.
2. **One live Greenhouse run** — prove the generic filler end-to-end. ISSUES → B.
3. **Host session for the deeper queue work** — per-job wall-clock cap + per-ATS
   URL validation. ISSUES → C, D.
4. **Ethan: keep listing bugs** — the list isn't empty yet. ISSUES → E.

Deferred code cleanups (do during the next host session, not blind): dedupe the
work/education card-fill loops in filler.py (~70 lines, needs live verification);
optionally expose `main.py summarize` in the JobAgent.bat menu or drop it.

## Ready-to-paste resume prompt (for Ethan)

> Continuing the job-search-agent. Read CLAUDE.md, CONTINUE_HERE.md and ISSUES.md
> first. I want to [pick one: do the PNC retest / do the Greenhouse live run /
> work the queue timeout with you / list more bugs I found].
