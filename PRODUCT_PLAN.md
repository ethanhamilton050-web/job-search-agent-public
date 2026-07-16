# Job-Search-Agent — Master Product Plan

*Last updated: 2026-07-02. Owner: Jane Doe.*
*This file is the source of truth for the product/business plan. It lives in the repo on purpose so any chat, tool, or person can find it — not buried in one assistant's private memory.*

---

## The product

Turn the working `job-search-agent` CLI into a **sellable high-throughput assisted-apply product.** The user queues many jobs; the software grinds through sign-in, account creation, and every application-wizard page unattended, paced for safety; then hands back a batch of finished applications. **The human reviews each one and clicks Submit.** That final click is deliberate and permanent — it is the legal firewall, the quality gate, and natural anti-ban pacing, all at once.

## Wedge vs. moat (don't confuse them)

- **Wedge (launch hook, NOT the moat):** automate the Workday **sign-in + account-creation gates** that competitors (Simplify, Teal, LazyApply) don't touch. Treat this as a **depreciating asset** — it's copyable, and Workday will harden signup (CAPTCHA / SMS / device-fingerprint) the more we succeed. It's the demo, not the pitch.
- **Durable value (build the company on these):**
  1. **The fact-validator / honesty layer** — never changes any number/date/$/metric on a résumé, and never fabricates an answer on the user's behalf. Provable, and now *demonstrated* (see Validation Log).
  2. **Cheap coverage breadth** via LLM form-mapping (Decision 1).
  3. **The review + tracking workflow** as a real product.

---

## Decision 1 — Escaping selector fragility (LLM form-understanding)

**Approach: LLM-as-compiler, not LLM-as-driver.**
- On first encounter with a form, dump its field structure and have an LLM produce a **field-map** (each field → its meaning → how to fill it).
- **Cache the map**, keyed by ATS type + page structure. Workday tenants are ~90% the same product, so we solve the *product*, not each tenant.
- **Runtime is deterministic** — the app just replays the cached map with the browser automation. No AI in the live loop.
- The LLM **re-engages only on drift** (a field moved, a validation error, a new field) and patches the cache — self-healing.

**Where the AI runs (this matters):**
- The LLM is **centralized on Ethan's server, never on the customer's PC.** Customers run only a browser replaying cached maps — no GPU, no AI on their machine.
- A **central field-map service** (a small database + lookup API) stores every known map. The app asks it "got a map for this page?" and **caches the answer locally**, so the app keeps working even when the service is down.
- Only NEW/drifted forms trigger an AI call, run server-side. **Only the blank form structure leaves the device** (scrubbed of any pre-filled values) — never the user's résumé, answers, or credentials. PII stays local.
- **Coverage compounds across all users** — every new form one user hits improves coverage for everyone. That's the moat.

**Hosting:** Ethan's spare Gemma box (RTX 5080) running 24/7 is fine for the beta — the AI is off the critical path, so its downtime only delays mapping brand-new forms; everything already cached keeps working. **Upgrade path:** when home-box downtime starts costing support, move the tiny always-on lookup service to a ~$5/mo cloud VM, leave the AI at home feeding it. Run on the spare box, not a daily driver.

**Model choice:** a **small, fast, free local model is good enough** — see Validation Log. No hosted model needed for routine pages. Debug logs become an eval/regression set, not fine-tuning data.

**The two-layer honesty guardrail (never trust the AI to police itself):**
- **Layer 1 (AI):** instructed to answer only what the profile supports and reply `NEEDS_HUMAN` when unsure — never guess.
- **Layer 2 (CODE, the real enforcement):** the AI must return a *receipt* (which profile field/line its answer came from). The code verifies the receipt is real; if not, it **overrides the AI and routes the question to the human**, regardless of AI confidence. Production form: each known question maps to a required profile field — an empty field forces human review, period. Same philosophy as the résumé fact-validator.

---

## Decision 2 — Deployment: LOCAL, not cloud

**Core principle:** automation runs on the **user's machine**, against their **real browser**, on their **real residential IP**, and their **credentials never leave their device.**

Cloud server-side browsers were **rejected**: datacenter IPs are the loudest bot signal there is, and holding every user's employer credentials + PII is the worst possible liability.

- **Primary product = desktop app** wrapping the existing Python + Playwright. It's the smallest leap from today, has full capability, and is the only form that can house the headline "queue N, walk away, come back to finished applications" unattended-batch feature. It also does résumé upload for free (Playwright's `set_input_files`), including Workday's custom widget.
  - **Browser driving = persistent owned profile:** the app ships its own browser profile; the user logs into their ATS accounts once, and cookies accumulate so it reads as a real returning user. (Chosen over hijacking their daily Chrome, which is intrusive and fragile.)
  - **Shell/build:** render the existing Flask dashboard in a native window via `pywebview`; package with PyInstaller (bundling Playwright's Chromium is the one fiddly bit); skip auto-update for v1.
  - **Three genuinely-new pieces to build vs. today:** the queue + pacing loop, the review UI (extends the dashboard), and packaging/signing.
- **Companion browser extension = DEFERRED** until the app proves demand (app first, not both at launch). Later it becomes the low-friction B2C top-of-funnel (one-click install, Web Store discovery) doing per-page autofill only; unattended batch stays app-only.
  - *Why not extension-first:* résumé upload can't be done cleanly in an extension (the reliable method reintroduces a scary "debugging this browser" banner + Web Store policy risk), and the sandbox fights unattended batch — the core feature.

---

## Rejected outright

- **IP rotation / residential proxies.** Increases ban risk (one applicant appearing from many cities is an anomaly) and hands a CFAA "circumvention" argument. Local architecture already gives each user a legitimate residential IP for free.
- Fingerprint/stealth evasion, CAPTCHA-solving, blind spray-and-submit, LinkedIn/Indeed automation.
- **Keep** per-user throttling/pacing as a *safety* measure — but it's only a soft signal; being genuinely real (local + real IP + real browser) is the real defense.

## Marketing guardrail

Hang the brand on **"we never fabricate anything on your résumé or your answers"** (content honesty — provable, and demonstrated). **Do NOT** market "we don't do sketchy automation" — auto-creating accounts and auto-clearing verification emails are grey by Workday's lights, so that framing is a hypocrisy trap.

## Market — B2C (Ethan's decision, against the B2B lean)

Survive the churn-on-hire treadmill by:
- Capturing value fast (annual-upfront / higher monthly).
- Using the **application cap as both pacing-safety and pricing lever.**
- Converting the "got hired" moment into referrals/testimonials.
- *(Noted for later: career coaches are the better B2B beachhead — B2B economics, B2C-easy sale, no churn-on-hire.)*

## Legal shield (same for app or extension)

**Runs on the user's machine · credentials stay local · human clicks Submit.**
- **Account-creation is the single highest-risk behavior** → that's where lawyer spend goes.
- Never add evasion infrastructure or outcome guarantees — those escalate a civil ToS dispute toward CFAA/FTC exposure.
- Realistic worst case = cease-and-desist + banned users + reputation damage, **not** prison — *provided* the line above is held.

---

## Validation log (what's been proven)

All tests used the real Citi Workday application, driven by `experiments/field_map_test.py` (a stdlib-only harness) against a local Gemma model. Input = the field dumps the app already logs; ground truth = the hand-built filler + Ethan's profile.

| Date | Test | Model | Result |
|---|---|---|---|
| 2026-07-02 | **Field mapping** (My Information page) | frontier | 14/14 fields (intent+strategy) |
| 2026-07-02 | Field mapping | `gemma4:26b` | intent 17/17, strategy 15/17 (2 misses = multiselect widgets, 1-line prompt fix), 112s |
| 2026-07-02 | Field mapping | `gemma4:e4b` (small) | **identical** result in 12s (~9× faster) |
| 2026-07-02 | **Screening questions** (9 real Citi legal questions) | `gemma4:e4b` | **9/9** in 6s — got the sponsorship double-negative + dense legal questions |
| 2026-07-02 | **Honesty safety-net** (incomplete profile) | `gemma4:e4b` | Flagged BOTH unanswerable questions as `NEEDS_HUMAN`, cited real profile lines for the rest — **zero fabrications** |

**Conclusion: Decision 1's feasibility is fully retired.** Form-understanding + hard-question reasoning + the honesty guarantee all work on a small, free, local model. The one open confidence-builder is breadth (only Citi tested so far).

**Caveat added 2026-07-09:** the 9/9 honesty-safety-net result above tested a
*curated* question set, not an *adversarial* one. An overnight adversarial
audit specifically hunting for topically-similar-but-wrong-topic questions
found real gaps in `jobagent/guardrail.py`'s Layer 1 (a felony/weapons
question was auto-answering via `is_veteran`; a background-check-consent
question via `work_authorized` — zero human review, since Layer 1 has no
receipt check) and Layer 2 (a receipt's field value was checked, but never
whether the field was actually relevant to the question). Both are now
fixed and re-verified — see `ISSUES.md`'s fixed log, 2026-07-09. The honesty
guarantee's *mechanism* (demand a receipt, never trust the model) was always
sound; its *pattern coverage* had real, demonstrated holes that only surfaced
under adversarial testing, not the original curated validation. Worth
remembering before treating any future "N/N passed" result as the full story
— test what should fail, not just what should pass.

## Open / next steps

**Built 2026-07-02 (overnight, code-side, all unit-tested):**
- ✅ Honesty guardrail as real code — `jobagent/guardrail.py` (Layer 1 resolver + Layer 2 receipt check), `tests/test_guardrail.py`.
- ✅ Field-map cache + local-LLM client — `jobagent/fieldmap.py` (cache-first, box-down → falls back to hand selectors), `tests/test_fieldmap.py`.
- ✅ Queue + pacing loop — `jobagent/applyqueue.py` + `main.py queue add|list|run` + dashboard **Queue/Review** tab, `tests/test_applyqueue.py`.
- ✅ Per-company safety cap — `jobagent/attempts.py`: never auto-applies to the same employer more than 3× (`CAP`), across both `apply` and the queue, so testing/batching can't flag a company you may want. `main.py attempts [show|reset]`, `tests/test_attempts.py`.
- ✅ Packaging scaffold — `app.py` (pywebview window) + `PACKAGING.md`.
- ✅ Trivial multiselect→select prompt fix in `experiments/field_map_test.py`.
- ✅ Screening pipeline — `jobagent/screening.py` runs each question through the guardrail (auto-answer or `NEEDS_HUMAN`); `tests/test_screening.py`. **Ready to wire into the live filler in a host session; not yet driving the browser.**
- ✅ Replay planner — `jobagent/workday/replay.py`: a pure, tested planner that turns a cached field map into a fill plan; `tests/test_replay.py`. **Logic only — NOT yet wired into the live browser.**
- ✅ Descriptor scrub — `fieldmap.scrub()` strips volatile ids so the map cache keys on stable page structure; `tests/test_scrub.py`.
- ✅ Fill report + Review detail — `jobagent/fillreport.py` stores what each run filled/flagged/errored (`tests/test_fillreport.py`); the queue filler writes it best-effort (observation only, can't affect a run) and the dashboard `/queue` shows "N flagged, M errors" per job (`tests/test_dashboard_queue_report.py`).

**Built 2026-07-08 (on the actual Windows host, not a container):**
- ✅ Desktop app freeze, fully offline — `pyinstaller --windowed --collect-all playwright
  --add-data "...ms-playwright;ms-playwright" app.py` produces a working `JobSearchAgent.exe`
  with Chromium bundled straight in (~835MB; Ethan's call — "don't care about size, just make
  it work"). Fixed a real bug found only by actually running the frozen build: it was
  resolving Chromium's path to an empty bundle-relative folder instead of the real shared
  cache, silently trying to re-download every launch. `app.py`'s `_ensure_chromium()` now
  checks bundled copy → system cache → download-as-last-resort, and does a REAL
  launch-and-close (not just a file-exists check). Pushing verification further (real
  navigation, not just launch) then caught a SECOND, bigger bug: the frozen app couldn't see
  any of your real `profile.json`/`config.json`/`jobs.db` at all (empty dashboard, "0
  listings") because `config.ROOT` resolved inside the frozen bundle's own internal folder,
  not your real project directory — would have shipped a completely non-functional app
  otherwise. Fixed (`config.py:_detect_root`); confirmed by copying real data into a frozen
  build and seeing the real listing count. Pushed once more and caught a THIRD bug: the
  Apply and Run-queue buttons shell out via `sys.executable` + a `main.py` path, both
  meaningless once frozen — clicking Apply would have silently relaunched the GUI instead
  of actually applying to a job. Fixed (`app.py` dispatches CLI args itself when frozen;
  `dashboard.py:_cli_command`); verified for real by running `JobSearchAgent.exe attempts`
  and seeing it print real data instead of opening a window. Verified across many real
  frozen builds total, never assumed a fix worked. See `PACKAGING.md` for detail.
- ✅ AFTER_LAUNCH's Match Coach (resume rewrite suggestions) built end-to-end — separate list,
  full detail in `AFTER_LAUNCH.md`.
- ✅ `scan` now auto-summarizes new jobs (best-effort, capped, silent if Ollama's off) — Ethan
  wants the whole tool as automatic as possible.

**Still open (need Ethan / the host):**
1. **Prove breadth (needs you):** run the filler on ONE new employer with the browser visible, capture the field dump, re-run the mapping test on it — confirms it's not a Citi fluke.
2. **Wire guardrail + field-map into the LIVE filler** (`jobagent/workday/filler.py`): I left this un-touched on purpose — it's 2,200 lines that can only be verified against a real browser. Needs a host session.
3. **Code-sign the frozen exe** (needs your identity + a certificate purchase — see `PACKAGING.md` §3) and prove the frozen app can drive a real Workday application end-to-end (needs a real login).
