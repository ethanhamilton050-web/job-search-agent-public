# After-Launch Backlog

Ideas we've agreed are worth doing but **not** for v1. This is a separate list from
`PRODUCT_PLAN.md` (the master plan / definition-of-done). Nothing here blocks launch;
we pull an item up into the master plan only when we decide to build it.

---

## 1. Resume "match coach" — JD-aligned, truthful tailoring

**The idea (Ethan, 2026-07-02):** Next to the match %, show *which* of the job's key
traits the resume already hits and which it misses. Then suggest ways to reword the
resume so it speaks the job posting's language — **without lying**, because a lot of
postings are now screened by software and the same real experience described in the
posting's own words scores/surfaces better.

### Fact-check: is the premise true? Mostly yes — with one myth to avoid

- **Screening software is near-universal at the employers we target.** ~97.8% of
  Fortune 500 use a detectable ATS, and **Workday is the #1 system (~39%)** — which is
  exactly who Ethan applies to (Citi, PNC, S&P, Travelers are all Workday). So "a lot of
  jobs are scanned by software" is correct.
- **The scary version is a myth — do NOT market it.** The "75% of resumes are
  auto-rejected by a robot before a human sees them" claim is false. 90–95%+ of
  applications *are* seen by a human; automatic rejections come from explicit knockout
  questions (work authorization, required license), **not** from keywords or formatting.
  Fear-based "beat the ATS bot" marketing would directly contradict our honesty moat, so
  we frame this as *"help the right humans and searches find you,"* not *"trick the robot."*
- **But terminology genuinely matters, for real reasons:** (a) recruiters **search/filter**
  the ATS by skill/title keywords — if your resume never uses the word they search, you
  don't surface; (b) systems that rank by match forward the high-match ones first; (c) a
  growing **AI/LLM layer** now summarizes and scores each resume's fit for the recruiter.
  The average resume matches only ~51% of a posting's keywords, so there's real room.
- **Measured lift is large (vendor stats — discount somewhat, but the direction is
  consistent):** tailored resumes get roughly **2× the callbacks** across 1.7M+
  applications; one 15k-application study found 11.7% vs 4.2% callback; Jobscan cites ~50%
  more. Even halved, that's a headline-worthy outcome.
- **The honest version is also the *better* version.** Modern/semantic screening
  understands synonyms, so "clear, specific, factual writing beats cramming exact
  phrases." **Keyword stuffing backfires** — systems don't reward repetition and 76% of
  recruiters penalize over-optimized resumes. So the winning move *is* the truthful one:
  describe the same real work in the posting's vocabulary, once, backed by real scope/
  numbers. That's our moat, not a compromise of it.

**Bottom line:** the premise holds. Reframe it from "beat the bot" to "say your real
experience in the words this posting and its recruiter are searching for."

### Feasibility

Splits cleanly into a cheap half and a harder half:

- **Half A — the match breakdown (matched / missing traits). Nearly free, already built.**
  The scorer already computes matched skills, missing skills, matched keywords, and title
  overlap, and already stores them in `score_reasons` (today they show on hover over the
  match %). Turning that into a visible ✓matched / ✗missing list is a small display change
  — no new AI, no resume editing. **Could be pulled forward to now/pre-launch** if we want.
- **Half B — the rewrite suggestions. Post-launch; it's its own mini-product.** Needs: read
  the user's real resume bullets (we have them in `profile.json`), find which ones already
  cover a "missing" JD trait in different words, and suggest a truthful rephrase in the
  posting's vocabulary. Reuses two things we already have — the **local Gemma box** (writes
  the suggestion) and the **fact-validator** (`guardrail.py`, already guarantees no number/
  date/$/metric changes). The genuinely new, high-stakes work is the **honesty guarantee on
  generative text**: the model must only rephrase what's actually supported and never invent
  experience — the same two-layer "receipt or it's flagged for a human" pattern from the
  master plan's Decision 1. Always **suggest + user approves each edit**; never silently
  rewrite. That keeps it inside the legal/quality firewall.

### Why post-launch (agreed direction)

v1's definition of done is *apply across ATS types, packaged app, website* — it doesn't
need the coach. Half B touches our most sensitive surface (generating resume text), so it
deserves its own focused build, not a rushed v1 corner. It's also a natural **premium /
credit-driver** upsell after the applier proves demand. Half A is cheap enough to pull
forward whenever we want a richer "why this score" in the review flow.

**Open risks to design against:** over-statement (suggesting a reword that claims more than
the person did), keyword stuffing, and users trusting a suggestion without checking it —
all handled by validator + mandatory human approval + "cite the resume line this came from."

_Sources: Jobscan 2025 ATS usage report; Enhancv "25 recruiters"; Interview Guys /
Hiration ATS-rejection-myth pieces; Brainner & atsverification on semantic/LLM screening;
InterviewPal / MokaHR / Jobscan on keyword stuffing; Resumly / scale.jobs callback stats._
