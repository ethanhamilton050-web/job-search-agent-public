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
status/plan/notes .md files — update the owner above. `SESSIONS.md` owns
*live* session state (see "Parallel sessions" below) — don't invent a second one.

## Parallel sessions — several terminals, one project

Ethan often has multiple terminals open on this repo at once. Only real hazard:
two terminals editing the **same file** (last save silently wins). `SESSIONS.md`
is a shared claim board that prevents that and doubles as Ethan's view of what
each terminal is doing. Every session, do this automatically:

1. **At task start:** read `SESSIONS.md`. Add your own `##` block — pick an
   unused `session-N` id, list the files you plan to touch, set `status: editing`.
2. **Before editing any file:** if another block with `status: editing` already
   claims that file, **don't edit it.** Tell Ethan it's in use, and pick other
   work or wait — the auto-backup (~12s) is the safety net, not an excuse to race.
3. **When done:** flip your block to `status: done` and leave the notes as the
   record. Tidy old `done` blocks when the board gets long.
4. **Showing the work (Ethan's not a coder — no raw diffs):** after a change, say
   in plain English what changed and why. If the change alters how the dashboard
   *looks*, hand him a screenshot PNG instead of describing it (screenshot helper
   is built on first need — headless Chromium is already in the container).

## Anti-regression protocol (this project's #1 pain)

1. **Run `.venv\Scripts\python.exe -m pytest -q` before AND after changes** (~2 s,
   all green). If a test in ISSUES.md's fixed log fails, an old fix regressed —
   restore it, don't re-solve from scratch.
2. **Every fix ships with a test** and a line in ISSUES.md's fixed log.
3. **Never edit `jobagent/workday/filler.py` blind.** It's ~2,600 lines that can
   only be verified against a real browser. Change it only in a live host session
   with Ethan driving, or when a unit test can prove the change.

## The honesty contract for asserted values — NEVER AGAIN (license/education class)

The tool put a **false claim about Ethan** on real applications twice: it ticked a combined
"Series 6/7" box (claiming a Series 7 he doesn't hold) and derived "MBA" from "MBA Candidate"
(claiming a degree he hasn't earned). A false fact about the applicant is as serious as
auto-submitting — treat it that way. **Any code that SELECTS a value asserting a fact about
Ethan** — a dropdown, checkbox, radio, or a derived field (licenses, education level, employer,
demographics) — MUST obey all three, and a change that doesn't is not done:

1. **Assert only what's PROVEN true.** On a compound / partial / ambiguous option (a box that
   names one credential he holds AND one he doesn't, an in-progress degree, a near-miss label),
   **leave it unselected and flag it for the human** — never auto-claim. Unsure = abstain =
   park, exactly like the screening guardrail. Code decides, and when code can't prove it, the
   human does.
2. **Ground the input.** The value comes from a source Ethan confirmed (My Info, résumé he
   reviewed + Saved), never re-typed guesswork, and never an "in progress / expected /
   candidate" value asserted as complete.
3. **Ship the adversarial test.** A clean-match test and a clearly-wrong test are NOT enough —
   both passed while the bug shipped. Every such selector needs a **partially-true / compound /
   near-miss** case in the suite. No adversarial test → the selector is unguarded.

Canonical fixes to copy: `applier._license_box_action` (`test_applier_finra.py`) and
`answer_bank._highest_education` (`test_profile_defaults.py`). Full method + the workspace-wide
version of this rule: the `ai-build-playbook` skill ("front + back guard", "a check is a command
that fails loudly"). This is the project's #1 pain generalized — do not weaken it.

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

## Reviewing stuck applications (the failure "black box")

Every autofill run drops its evidence into its own folder under `output/traces/`
(via `jobagent/failpacket.py`): the screenshots, a plain-English `NOTES.md` (the
on-screen errors + what page it stopped on + the last actions it took), and one
line appended to `output/traces/INDEX.md`. Capture is observation-only — it can
never change or break an apply run. (Real capture only happens on the host where
the browser runs; in the container you read the folders it produced.)

`NOTES.md` also lists **the questions the bot couldn't answer**, each tagged with
the screenshot it appeared on — so you can *see* the question, not just read it.
Those same questions are parked by `qbank` on the dashboard `/answers` page; once
Ethan answers one there, qbank recalls it on every future application (that
learn-once loop already exists — this just gives it eyes).

When Ethan says **"review the last runs"** / **"review the stuck applications"**:
1. Read `output/traces/INDEX.md` — recent attempts, outcomes, and question counts.
2. Open each flagged folder: **look at the PNGs** and read `NOTES.md`. For each
   parked question, look at its screenshot and either help Ethan word a good
   one-time answer, or improve how the bot recognizes that field (e.g.
   `_clean_question`, guardrail rules). Diagnose in plain English, no raw diffs.
3. Write anything worth keeping as a line in **`ISSUES.md`** (the owner — don't
   start a new notes file).
4. Any code fix ships **with a guarding test** (anti-regression rule above). A fix
   touching `filler.py` must be test-backed or host-verified — never blind.
Nothing here changes the no-auto-submit gate.

## Code map

- `main.py` — CLI (scan/list/tailor/render/apply/queue/status/export/doctor)
- `dashboard.py` — Flask web view (port 5000); `app.py` — same thing in a native
  pywebview window (packaging target)
- `jobagent/` — `scorer` `tailor` `guardrail` (fact-validator) `screening`
  `fieldmap` (LLM form-map cache) `applyqueue` `attempts` (cap) `database`
  `matchcoach` (resume-bullet rewrite suggestions, gated by `tailor.validate`)
  `linkcheck` (per-ATS URL shape validation before an unattended browser launch)
  `failpacket` (per-attempt evidence folder + INDEX.md — see "Reviewing stuck applications")
  `sources/` (greenhouse, lever, workday feeds)
- `jobagent/applier.py` — generic single-page filler (Greenhouse/Lever/Ashby)
- `jobagent/workday/` — `filler.py` (the wizard driver — see rule above),
  `inbox.py` (Gmail IMAP verification), `answer_bank.py`, `replay.py`
- `tests/` — pytest suite; `experiments/field_map_test.py` — LLM eval harness
  (imported by tests, keep)
