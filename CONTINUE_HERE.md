# Continue Here — session resume point

*Overwrite each session. Last updated **2026-07-15 (session 25)**. **363 tests pass**
(`.venv\Scripts\python.exe -m pytest -q`). Read order:*
`CLAUDE.md` → this file → `ISSUES.md` → `PRODUCT_PLAN.md` → `AFTER_LAUNCH.md`.

*(The big session-10 audit menu — F1–F15, U1–U12, C1–C6 — was retired from this file;
it lives in git history and the surviving items are tracked in `ISSUES.md`.)*

---

## What just happened (sessions 19–24)

The "grounded intelligence" feature — answer screening questions from facts Ethan gives
ONCE, never guess — was built out and honesty-guarded across sessions 20–23, then given a
small usability upgrade in 24. **It is fully built and tested but INERT until Ethan re-saves
My Info** (see "Blocked on Ethan" below):

- **Grounded answers (B/C + values):** education, home country, state of residence, full
  work history ("ever worked at X?"), current-employer, public-company insiders,
  employer-relatives, government officials, professional disclosures — each answers only
  when Ethan's facts PROVE it, else parks. ~21 of his 33 parked questions now auto-answer.
- **FINRA license front+back honesty guard (session 23):** My Info pre-fills licenses from
  his résumé (`Series 6, 63, SIE, Insurance Producer` — **no Series 7**); the checkbox
  picker (`applier._license_box_action`) ticks a box only when EVERY credential it names is
  held, and **leaves a combined `Series 6/7` box unticked + flagged** rather than falsely
  claim Series 7. Host-verified live on SoFi.
- **Session 23 also:** found+fixed the education-derivation bug (in-progress "MBA Candidate"
  → "MBA" claimed as completed — now parks) and ran a workspace-wide honesty audit of the
  other projects (scorecard delivered to Ethan; NO fixes made there — awaiting his go).
- **Session 24:** "+ add another" buttons on My Info's insider / relative /
  official sections so the fixed 3/2 blank rows don't run out (front-end only; parser
  already took any N). +1 test.
- **Session 25 (this one — dashboard bug/UX pass, all browser-verified live):** fixed a
  real data-loss bug (a salary answer typed as a NUMBER was wiped by the next /answers
  Save — the strategy dropdown couldn't display it); queue "Updated" times now show in
  LOCAL time (were raw UTC, hours off); an empty job list now explains itself instead of
  a bare table; /answers Save now confirms with a "✓ saved" chip like /profile. +7 tests
  (incl. the adversarial literal-number and capitalized-strategy near-miss cases). No
  filler.py / live-ATS work. Dashboard on port 5000 was restarted twice during testing
  and STOPPED at session end — the port is free.

---

## ⭐ Active thread: "grounded intelligence" — answer from known facts, never guess

Ethan reviewed all 33 questions parked across recent runs and wants the tool to *use
facts he's already given* instead of parking them. **This is not guessing** — it's using
provided data + recognizing that a reworded question is an old question. Everything the
tool still can't ground stays parked (the honesty firewall is untouched). His 33 → **4
buckets**:

- **Bucket A — "you already told me this"** — ✅ **DONE.** Increment 1: work-auth
  "eligible/legally" + sponsorship "immigration" rewordings. Increment 2: `value_answer`
  grounded VALUES for **highest education** + **home-address country**. Increment 3:
  `current_employer_answer` ("currently a <co> employee?" → safe No). All in `guardrail.py`,
  wired into `applier.py` via `_grounded_answer`, tested.
- **Bucket B — "ever worked at X?"** — ✅ **mechanism DONE** (`ever_worked_answer`): Yes if
  the company is in the affirmed-COMPLETE `employers_all`, No only when complete, else park.
  A resume is NOT used (shows only some jobs). **Needs Ethan's data** (see below).
- **Bucket C — "related-party"** — ✅ **mechanism DONE** (`related_party_answer`): relatives-
  as-insiders / self-insider / government-official → No only when the list is empty AND
  `related_party_complete`; any entry parks. **Needs Ethan's data** (see below).
- **Bucket D — "same question, new words"** (AI reword-match) — **DEFERRED** by decision:
  build after real usage shows how many questions actually still park. When built: Claude
  API, question-wording only (no PII), conservative + guardrail-validated.

### ✅ To ACTIVATE Buckets B & C: Ethan fills the dashboard **My Info** page (`/profile`)
No JSON — a form (built session-21) captures highest education, home country, full work
history (+ "this is complete" checkbox), public-company **insiders with the ownership
amount** (self or relative), and government officials (+ "listed everything" checkbox).
Saves to `grounded_facts.json` (gitignored, SEPARATE from the creds file). Until the two
completeness checkboxes are ticked, B/C safely PARK (the honesty default).
**Ethan's running dashboard is a STALE instance — he must restart `python dashboard.py`
to see /profile and get the new grounded answers.**

**Result on the 9 SoFi questions (synthetic complete profile): 5/9 now auto-answer**
(sponsorship, worked-at-SoFi, current-SoFi-employee, employed-by-Deloitte, home country);
the other 4 correctly park (marketing consent, SMS consent, FINRA licenses, relocation).

**My recommendation to him (pending his decision, NOT yet a committed product rule):**
PII (resume, answers, personal facts) **never leaves the machine**; only a form's
**question wording** (no PII) may ever go to a model, and only when rules can't handle it.
That keeps "your data stays local" intact even if the Claude API is used for matching.
Build the no-AI buckets now; defer the AI matcher; when added, Claude API, question-text
only, conservative + guardrail-validated. *(If Ethan confirms this, write it into
`PRODUCT_PLAN.md`.)*

---

## ⛔ Blocked on Ethan (nothing new ships value until this happens)
**Ethan restarts the dashboard + re-saves the My Info page** (`/profile`). His running
dashboard is a STALE instance — it predates all of the grounded-facts work and the new
`/profile` fields. Until he restarts (`python dashboard.py`) and clicks **Save** (with the
two "I've listed everything / complete history" boxes ticked), every grounded answer safely
PARKS instead of firing — so ~21 auto-answers stay dormant on his real applications. This
one action is the gate on the whole feature. (`employers_all` is already pre-seeded from his
résumé; the new "+ add another" buttons handle >3 insiders/officials.)

## Next steps (pick one, after the block above)
1. **A live host run** (`--auto-close --trace`) on an embedded form once My Info is saved —
   watch the grounded answers fill against his real profile. (Needs the Windows host browser.)
2. **Bucket D** (AI reword-match) — only after real usage shows the remaining parked long-tail.
3. **Other-project honesty fixes** from the session-23 workspace audit (reconbot false-green,
   CUCS score, CUs SAR/CTR, Market Report blank-green) — all await Ethan's prioritization +
   host verification; several are LIVE/compliance, do NOT touch blind.

**Anti-regression:** run `pytest` before/after; every fix ships with a test + an
`ISSUES.md` log line; never edit `jobagent/workday/filler.py` blind (that's the Workday
wizard — `applier.py` is the generic one and is unit-testable).
