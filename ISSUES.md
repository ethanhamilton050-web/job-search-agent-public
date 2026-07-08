# Issues

Open work first; everything fixed is one line in the log at the bottom with the
test that guards it. **Anti-regression rule: before "fixing" anything, check the
fixed log and run `pytest` — if a listed test fails, the old fix regressed; make
that test pass again rather than re-solving from scratch. Every new fix ships
with a test and gets a line in the log.**

---

## Open

### A. "How Did You Hear About Us?" — fix written, needs ONE live retest
Required picklist is per-employer (Citi = "Website > Citi Jobs Career Site",
PNC = "Corporate Website > PNC Career Site"), so the old hardcoded Citi value
blocked PNC. New `_fill_source` (filler.py) uses the configured value if it
matches, else the first available option (non-material field — any answer is
fine). Re-run a PNC apply to confirm.

### B. Greenhouse autofill — never live-proven, likely unblocked
The generic single-page filler (`jobagent/applier.py`, handles
Greenhouse/Lever/Ashby) exists and now reaches real forms after the canonical-URL
fix (log #3). Greenhouse forms are uniform (first/last/email/phone/resume), so it
should work. Needs ONE live Greenhouse run to confirm.

### C. Per-job wall-clock cap — the deeper half of the queue-freeze fix
Reach-form now fails fast (120 s unattended, log #5), but a job can still be slow
*inside* the wizard (~14 steps × up to ~20 s waits each). Real fix = a whole-run
per-job timeout in the unattended path. Also confirm 120 s survives a real slow
email round-trip. Touches the live browser driver → do WITH Ethan on the host,
not blind. Pairs with D.

### D. Link accuracy — needs research (Ethan flagged)
Dead/wrong/non-application links waste a browser launch + hang. A naive
pre-flight reachability check is risky (Workday/Greenhouse bot-protection can
403 a bare request → false-positive skipping real jobs), so not added. Real fix =
per-ATS URL validation. Pairs with C.

### E. Ethan has more issues to list
He said "a lot"; only the ones above have surfaced. Finish emptying the list
before starting deeper live work.

---

## Fixed log (newest first — one line + the guarding test)

| Date | What | Guarding test |
|---|---|---|
| 2026-07-02 | "Verify account before sign-in" loop (PNC): `_account_unverified` detects banner, resends link, Gmail path opens it — **confirmed live** | `tests/test_inbox.py` |
| 2026-07-02 | Unattended reach-form fail-fast: 120 s cap (was 300 s), bails → grinder marks 'error' and moves on | `test_reach_timeout_fails_fast_when_unattended` |
| 2026-07-02 | One hung job froze the queue: `applyqueue.reset_stuck()` auto (>15 min) + manual "Reset stuck jobs" button; queue page auto-refreshes with "N of M done" | `tests/test_applyqueue.py` |
| 2026-07-02 | `scan` never cleaned the DB (70% cruft, 3452→1058 rows): `database.prune_stale_listings()` wired into scan, keeps applied/queued + failed-source rows | `tests/test_prune.py` |
| 2026-07-02 | Greenhouse "Apply" opened marketing sites: build canonical `boards.greenhouse.io/<board>/jobs/<id>` URL (re-scan done) | `test_greenhouse_forces_canonical_url_over_overridden_absolute_url` |
| 2026-07-02 | `+queue` enqueued but nothing ran it: added `/queue/run` route + "Run queue now" button (double-click guarded) | `test_queue_run_launches_when_queued`, `test_queue_run_noop_when_empty` |
