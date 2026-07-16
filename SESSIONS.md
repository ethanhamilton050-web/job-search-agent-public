# Live sessions board

**What this is:** when several terminals are open on this project at once, each
Claude session writes what it's doing here so they don't edit the same file at
the same time — and so Ethan can see, at a glance, what every terminal is up to.

**How to read it:** each `##` block is one terminal. `status: editing` means that
terminal is actively changing the listed files — leave those files alone.
`status: done` means it finished; the notes are a record of what it did.

Rules for Claude sessions are in CLAUDE.md → "Parallel sessions". Clear out
`done` blocks whenever the list gets long — this is a live board, not a log.

---

## [session-26]
status: done
Honesty-contract fix from a cross-workspace review: `_degree_aliases` had no MBA
forms (an MBA holder could get "Master of Science" clicked — the exact
license/education class) and "Juris Doctor" fell through to the PhD branch
(would click Ph.D. forms). Both now have first-checked alias branches; no
MBA/JD want substring-matches an M.S./Ph.D. option. Pure-function fix +2
adversarial tests — `_pick_prompt`/DOM untouched (unit-test-provable path per
the never-edit-filler-blind rule). 365 tests pass (+2). Touched:
`jobagent/workday/filler.py` (_degree_aliases only), `tests/test_filler_parse.py`,
`ISSUES.md`, `CLAUDE.md` (filler line count ~2,400→~2,600).
**Left for Ethan (product decisions, from the same review):** (1) veteran EEO
default maps decline→"I am not a veteran" (an assertion) instead of a real
don't-wish-to-answer option — test-pinned as intended, so changing it is his
call; (2) applier.py's generic work-auth/sponsorship matching bypasses the
guardrail anchoring; (3) auto-ticked Terms consent + auto-signed CC-305 form.

## [session-25]
status: done
Dashboard bug/UX pass, all changes verified live in the agent browser (DOM reads — the
preview pane can't screenshot this session, same as 24). (1) DATA-LOSS FIX: a salary
answer typed as a literal NUMBER was silently wiped by the next /answers Save (the
strategy dropdown couldn't display it → browser submitted "" over it); literals now keep
a text box, and a capitalized strategy word ("Average") now selects its option instead of
being wiped the same way; clearer option copy. (2) Queue "Updated" stamps now LOCAL time
(were raw SQLite-UTC, hours off) via a `localtime` template filter. (3) Empty job list
now explains itself (show-all link + `main.py scan`) instead of a bare table. (4) /answers
Save confirms with a "✓ saved" chip like /profile. 363 tests pass (+7, incl. adversarial
near-miss cases per the honesty contract). Touched: `dashboard.py`,
`tests/test_dashboard_answers_widgets.py`, `tests/test_dashboard_queue_report.py`,
`tests/test_dashboard_empty_state.py` (new), `ISSUES.md`, `CONTINUE_HERE.md`. Dashboard
server STOPPED at session end — port 5000 is free for Ethan's own restart.

## [session-24]
status: done
"+ add another" buttons on My Info (insiders / employer-relatives / government-officials) so the
fixed 3/2 blank rows don't run out. Front-end only — `addRow()` clones the last blank `.grid` and
clears it; parser (`facts._rows`) already accepted any N, so no backend change. Verified the server
renders all 3 buttons + script + clonable rows (did NOT live-POST — that would overwrite Ethan's
real grounded_facts.json). 356 tests pass (+1). Touched: `dashboard.py` (PROFILE_PAGE only),
`tests/test_facts.py`, `ISSUES.md`, `CONTINUE_HERE.md` (was 5 sessions stale — refreshed). Browser
preview pane was dead this session (0x0, headless) so no screenshot — verified via server render.

## [session-23]
status: done
Pre-fill FINRA/securities licenses on My Info from the resume so Ethan doesn't re-type them,
same pattern as education/employers. `answer_bank._licenses_from_text` (Series exams incl.
"6 & 63" shorthand, SIE, insurance producer w/ anchor + `\b` year guard) →
`profile_defaults()["finra_licenses"]`; `/profile` falls back to it (saved answer wins).
Human still reviews + Saves before it reaches the filler. Verified on his real résumé →
`Series 6, Series 63, SIE, Life & Health Insurance Producer` (NO Series 7).
**BACK HALF also done:** the license checkbox picker (`applier._fill_license_checkboxes`) now
ticks a box ONLY when every credential it names was declared; a combined `Series 6/7` box (which
would falsely claim Series 7 for a Series-6 holder) is left unticked + logged "LEFT FOR YOU".
New `_label_series_nums` (reads ALL numbers incl. `6/7`, `\b`-year-guarded) + `_license_box_action`
(tick/park/skip); replaced `_license_matches`. 353 tests pass (+5 total this session). Touched:
`jobagent/workday/answer_bank.py`, `dashboard.py`, `jobagent/applier.py`,
`tests/test_profile_defaults.py`, `tests/test_applier_finra.py`, `ISSUES.md`.
**Ethan: open My Info and click Save** so the pre-filled licenses land in grounded_facts and
reach the filler.
**PLAYBOOK PATCHED** (cross-project): added to `_skills/ai-build-playbook/SKILL.md` — "guard
scope = consequence not model" (deterministic selectors that assert a user fact need the same
abstain-or-park contract as LLM steps), the front+back dual-guard, adversarial "partially-true"
coverage, and a review/build checklist. This is the workspace-wide rollout mechanism (skill is
consulted before any AI/check work in any folder). NOT a blind 14-repo code sweep. **AUDIT DONE (this project's selectors):** salary/residence (`smartanswer`+`salary`) = SOLID
(abstains, self-checked, truthful); EEO/self-ID (`_decline_select`/`_DECLINE_RE`) = SOLID
(declines by default, never asserts a demographic he didn't set); how-did-you-hear (`_fill_source`)
= low-risk/non-material. **FOUND + FIXED: education derivation asserted IN-PROGRESS degrees as
completed** ("MBA Candidate"→"MBA", "PhD student ABD"→"Doctorate") — same class as the license
bug; now parks unless the degree is completed (+2 tests). 355 tests pass. **WORKSPACE AUDIT COMPLETE** (4 subagents, read-only): scorecard delivered to Ethan. Unifying
finding = the "50/50" pattern is real: checks that work end in a runnable PASS/FAIL command;
theater = prose nothing runs, self-tests scoped to the cheap layer, or FALSE GREENS. Scariest:
reconbot prints "RECONCILED/IN BALANCE" on coincidental totals with ZERO matches (empirically
reproduced); CUCS /5 compliance score is negation-blind keyword-presence; CUs (SAR/CTR) has 0
tests on the flag-firing aggregation + silent OCR under-detection; Market Report Bot posts a blank
GREEN report to LIVE Discord if the data feed fails (selftest never checks data). Arb bot mostly
SOLID but range/complete-set hole-provers are switched off (false alerts only, until execution
enabled). Playbook patched again ("a check is a command that fails loudly": prose≠check,
scope-to-verdict-layer, false-green + forced-fail-test rule). NO fixes made to those projects —
awaiting Ethan's prioritization (several are LIVE/compliance, need his go + host verification).

## [session-22]
status: done
FOLLOW-UP-2: 3 more Ethan flagged now auto-answer — state of residence (`value_answer` from
`state` on file), FINRA licenses + Robinhood compound a–e (new `disclosure_answer` +
`professional_disclosures` My Info field). 21/33 now grounded (was 18). 343 tests pass.
Touched also `jobagent/applier.py`, `jobagent/facts.py` (professional_disclosures).
---
Reviewed all 33 parked questions vs. the grounded logic → 18 now auto-answer, weeded
NON-DESTRUCTIVELY (dashboard `_grounded_answers` marks them "auto-answered from your info"
+ drops the "need you" count; qbank entries stay since save() merges). Broadened insider
recognition to ANY % (5% SEC / 10% Section 16 — confirmed from real Robinhood/Citi
questions) + fixed 3 over-fires the review found: non-compete "former employer" (was "No"),
Citi "relatives working for Citi" (was resolving via current-employer rule), Robinhood
compound a–e (blanket "No" → parks). Added `employer_relatives` field/section (relatives who
WORK at a prospective employer, distinct from public-company insiders). 340 tests pass (+7).
Verified live: /answers shows 18 auto-answered, 15 needs-answer. Touched: `jobagent/guardrail.py`,
`jobagent/facts.py`, `dashboard.py`, `tests/test_guardrail_grounded.py`,
`tests/test_dashboard_answers_badge.py`. **Ask Ethan:** re-open My Info + re-save so the "I've
listed everything" affirmation covers the new employer-relatives section.

## [session-21]
status: done
Dashboard "My Info" page (`/profile`) — Ethan enters grounded facts once, no JSON. New
`jobagent/facts.py` (grounded_facts.json, gitignored, SEPARATE from the creds file);
`build_answers` merges it; reworked `related_party_answer` (unified `insiders` list w/
ownership amount + much broader phrasings per Ethan: insider = officer/director/10%+ owner
of a PUBLIC company, self OR relative). Verified end-to-end (Flask test client render→save→
guardrail reads it; live read_page render). **328 tests pass** (+10). Touched:
`jobagent/facts.py` (new), `dashboard.py`, `jobagent/guardrail.py`,
`jobagent/workday/answer_bank.py`, `.gitignore`, `tests/test_facts.py` (new),
`tests/test_guardrail_grounded.py`. **NOTE for Ethan's running dashboard:** it's a STALE
instance (pre-dates these edits, 404s on /profile) — restart it (`python dashboard.py`) to
get the My Info page + the new grounded answers.
**FOLLOW-UP (Ethan's catch):** the My Info form now PRE-FILLS from the résumé
(`answer_bank.profile_defaults()` → highest-education level, home country, seeded employer
list) so known facts aren't re-typed; saved answers still win. Verified live on his real
profile (derives "Bachelor's Degree" + country + 5 employers). 334 tests pass (+6).
Added `tests/test_profile_defaults.py`. NOTE: `grounded_facts.json` now holds Ethan's REAL
entered data — do NOT clear it.

## [session-20]
status: done
"Grounded intelligence" Buckets A(values)/B/C — honesty-first: answer screening questions
only from facts Ethan provides ONCE, else park. New resolvers in `guardrail.py`
(`value_answer` education+home-country, `ever_worked_answer`, `current_employer_answer`,
`related_party_answer`), one shared `_grounded_answer` wired into BOTH fill paths in
`applier.py`, one-time fields documented in `answer_bank.py` template. On the 9 SoFi
questions (synthetic complete profile): **5/9 auto-answer** (was 0); the rest correctly
park. Self-check caught + fixed an over-fire (a conditional "…licenses if employed by
SoFi" was answering No). **323 tests pass** (+13). Touched: `jobagent/guardrail.py`,
`jobagent/workday/answer_bank.py`, `jobagent/applier.py`, `tests/test_guardrail_grounded.py`
(new), `tests/test_applier_park_reuse.py` (signature), `ISSUES.md`, `CONTINUE_HERE.md`.
**Open:** Ethan fills `employers_all`/`employment_history_complete` + related-party lists in
`workday_answers.json` to activate B/C on his real data; Bucket D (AI reword-match) deferred.

## [session-19]
status: done
Module 0 of Ethan's AI-consultant learning plan (`..\_learning`): turned
`experiments/field_map_test.py` from a hand-run probe into a GATED eval — tunable
pass bars (field-map 90% / screening 100% / honesty = zero fabrications), a
`--gate` that runs all 3 scenarios, logs a dated row to `eval_log.csv`, and exits 1
on FAIL. `--demo` now also proves the gate logic offline. Guarded by new
`tests/test_eval_gate.py` (4 tests); full suite green. Touched:
`experiments/field_map_test.py`, `tests/test_eval_gate.py`. Reusable method
captured as the new `ai-build-playbook` skill (`..\_skills`, junctioned live). No
file collision with session-20 (guardrail.py / applier.py).

## [session-18]
status: done
Made the generic filler (`applier.py`) reach embedded forms + reject cookie banners.
(1) `_dismiss_cookie_banner` — Reject/Decline only, never Accept (privacy-safe). (2) The
real blocker: SoFi/Betterment/Brex redirect their Greenhouse link to a branded careers page
that EMBEDS the form in a `job-boards.greenhouse.io/embed/job_app` iframe. New `_form_root`
returns the page-or-frame and the fill runs inside it (react-select Escape → `combo.press`).
**Live-verified headless (dummy data, no submit): first_name/email filled INSIDE the iframe
on SoFi/Betterment/Brex; resume slot found.** (3) Genuine no-form pages (Fireblocks marketing
page) now say "NO FORM — open it yourself" instead of a fake fill. 308 tests pass (+11).
Touched: `jobagent/applier.py`, `tests/test_applier_cookie_banner.py` +
`tests/test_applier_form_root.py` (new), `ISSUES.md`. Also enabled the ponytail statusline
badge in `~/.claude/settings.json`. **HOST-VERIFIED** live on SoFi (real profile,
`--auto-close --trace`): filled the embed-iframe form with real data, work-auth "Yes",
9 SoFi-specific Qs honestly parked, nothing submitted. Screenshot in
`output/traces/attempt-20260714-145140/`. **Open next (optional):** the parked SoFi
questions now sit on the dashboard /answers page — answer once to auto-fill next time;
and a `--keep-open` run if Ethan wants to review+submit one himself.

## [session-17]
status: done
Taught the generic Greenhouse filler to OPERATE Carta-style react-select dropdowns
(Ethan's #1). New `_react_select_questions`/`_operate_react_select`/`_fill_react_selects`
in `applier.py` (open menu → click matching `.select__option`; qbank→guardrail→operate,
else park; multi-selects park; phone-widget list can't be mis-clicked). `_empty_fields`
excludes react-select internal inputs. **Verified live on Carta:** sponsorship auto-selected
"No", others parked with real labels. 297 tests pass (+5). Touched: `jobagent/applier.py`,
`tests/test_applier_react_select.py`, `tests/test_applier_park_reuse.py` (fake), `ISSUES.md`.

_Older sessions (11–16) are recorded in ISSUES.md's fixed log — cleared from this board._
