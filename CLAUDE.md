# Job Search Agent — read this first

Personal job-hunt automation for Jane Doe: scan job boards → score against
his resume → tailor docs (fact-guarded) → autofill applications (Workday wizard +
generic single-page) → human reviews and clicks Submit. Being turned into a
sellable desktop product (see PRODUCT_PLAN.md).

## Who you're working with

Ethan is a finance professional, **not a coder**. Explain in plain English with
analogies. He decides, you build. Don't show him diffs and expect a review —
describe what changed and why.

## Doc map — one owner per topic, don't duplicate across files

| File | Owns | Update when |
|---|---|---|
| `CONTINUE_HERE.md` | Where the last session left off + next steps | **Overwrite** each session |
| `ISSUES.md` | Open bugs + one-line fixed log w/ guarding tests | A bug is found or fixed |
| `PRODUCT_PLAN.md` | Business/product decisions (the master plan) | A decision changes |
| `AFTER_LAUNCH.md` | Deferred backlog (post-v1) | An idea is deferred/promoted |
| `README.md` | User manual: setup + daily commands | A command/flow changes |
| `PACKAGING.md` | Desktop-app build notes | Packaging work happens |

Status/progress goes ONLY in CONTINUE_HERE.md and ISSUES.md. Never create new
status/plan/notes .md files — update the owner above.

## Anti-regression protocol (this project's #1 pain)

1. **Run `.venv\Scripts\python.exe -m pytest -q` before AND after changes** (~2 s,
   all green). If a test in ISSUES.md's fixed log fails, an old fix regressed —
   restore it, don't re-solve from scratch.
2. **Every fix ships with a test** and a line in ISSUES.md's fixed log.
3. **Never edit `jobagent/workday/filler.py` blind.** It's ~2,400 lines that can
   only be verified against a real browser. Change it only in a live host session
   with Ethan driving, or when a unit test can prove the change.

## Hard rules

- **Never open or print** `.env`, `.gmail_app_password`, `workday_answers.json`
  (credentials). `profile.json` is Ethan's personal data — read only if the task
  requires it, never echo it back.
- **The tool never auto-submits an application.** Stopping at Review for a human
  click is the legal firewall (PRODUCT_PLAN.md). Don't weaken it.
- Browser/autofill work needs the **Windows host** (real browser + logins), not a
  container.
- **Testing cap gotcha:** repeat apply runs on one employer silently stop at 3
  (`jobagent/attempts.py` CAP — "ran then closed, no log"). Reset:
  `python main.py attempts reset "<company>"`.
- A background watcher (`..\_backup\backup-watch.ps1`) auto-commits this repo on
  every change ("auto-backup" commits) and pushes to the private GitHub repo
  `job-search-agent` (first pushed 2026-07-08 after a 3-week false-positive
  block). If backups ever stop, check `..\_backup\logs\BLOCKED-job-search-agent.txt`
  before assuming anything is wrong here. Don't add another backup scheme; keep
  `.gitignore` covering anything that shouldn't be swept in. `config.json` IS
  tracked — never let a real key/password land in it; secrets go in gitignored
  files (.env, .gmail_app_password, workday_answers.json).

## Code map

- `main.py` — CLI (scan/list/tailor/render/apply/queue/status/export/doctor)
- `dashboard.py` — Flask web view (port 5000); `app.py` — same thing in a native
  pywebview window (packaging target)
- `jobagent/` — `scorer` `tailor` `guardrail` (fact-validator) `screening`
  `fieldmap` (LLM form-map cache) `applyqueue` `attempts` (cap) `database`
  `sources/` (greenhouse, lever, workday feeds)
- `jobagent/applier.py` — generic single-page filler (Greenhouse/Lever/Ashby)
- `jobagent/workday/` — `filler.py` (the wizard driver — see rule above),
  `inbox.py` (Gmail IMAP verification), `answer_bank.py`, `replay.py`
- `tests/` — pytest suite; `experiments/field_map_test.py` — LLM eval harness
  (imported by tests, keep)
