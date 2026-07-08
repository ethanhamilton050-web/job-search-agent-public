# Job Search Agent

A personal job-hunting assistant: pull jobs from legit sources, score them
against your resume, generate **tailored** application docs (verbiage-only,
with a hard guardrail that no number/date/fact ever changes), and track every
application — all human-in-the-loop, no ToS-violating scraping or auto-submit.

## Why this design

- **No bot-spamming.** Sources are key-free company boards (Greenhouse, Lever)
  and public Workday job feeds. LinkedIn/Indeed block bots and ban accounts, so
  we don't scrape them.
- **Tailoring uses your Claude Max sub, not an API key.** The tool prepares a
  brief; you paste it into a Claude Code session; Claude returns a tailored
  resume; the tool *validates* it before saving.
- **Truthfulness is enforced.** Every number, %, $, date, and metric in your
  master resume is extracted and checked against the tailored version. Adding,
  changing, or dropping any of them **fails validation**. You also approve a
  before/after diff. (The two things software can't catch — a true number
  reattached to a false claim, or two numbers of the same kind swapped so the
  multiset is unchanged — are why you read the diff.)

## Setup

On Windows, double-click **`Setup.bat`** — it creates the virtual environment,
installs everything (including Playwright + Chromium for Workday), and runs a
preflight check (mostly `[ OK ]` lines at the end). `python main.py doctor`
any time tells you what's missing. By hand instead:

```bash
pip install -r requirements.txt
cp config.example.json config.json     # edit targets + company board slugs
```

Put your resume (PDF or DOCX) in `input/`, then:

```bash
python main.py setup        # parse resume + targets -> profile.json (REVIEW IT)
```

Open `profile.json` and fix any parsing mistakes — it's the source of truth.

## Daily flow

> On Windows, double-click **`JobAgent.bat`** for a menu that runs everything
> below (and sets up the environment on first run).

```bash
python main.py scan                       # pull + score jobs from config sources
python main.py list                       # ranked matches with fit reasons
python main.py tailor <id>                # prints a brief to paste into Claude Code
#   -> save Claude's tailored resume to output/tailored/<id>_draft.txt
python main.py render <id>                # validates the draft + writes resume.docx
python main.py status <id> applied        # found|tailored|applied|interview|offer|rejected
python main.py export                     # output/application_tracker.xlsx
python dashboard.py                       # web view (apply + status) at 127.0.0.1:5000
```

## Application autofill (run on your Windows host)

`python main.py apply <id>` auto-detects the employer's ATS and uses the right
filler: **Greenhouse / Lever / Ashby** (and most others) get the single-page
filler in `jobagent/applier.py`; **Workday** URLs get the multi-page wizard in
`jobagent/workday/filler.py`. Both fill what they can, then **pause for you to
review and submit** — neither ever auto-submits.

### Workday wizard

Big finance employers use Workday, which blocks bots — so you find the posting on
the company site, it redirects to Workday, and the filler drives the **whole
application wizard**: signs in (or creates the account), fills My Information,
uploads your resume, fills work history + education, answers self-ID/EEO to your
stated preference, best-effort answers screening questions — then **stops on the
Review page for you to submit**. It never auto-submits.

**One login everywhere.** Workday accounts are per-employer (each company is its
own tenant — you can't share a single login literally). So instead you set ONE
email + password in `workday_answers.json`; the filler signs in with them on each
employer, or creates the account there if it's your first time. A persistent
browser profile (`.browser-profile/`, git-ignored) keeps you logged in to
employers you've already applied to.

```bash
# one-time:
pip install playwright && playwright install chromium
python main.py workday-init                # writes workday_answers.example.json
#   -> copy to workday_answers.json; set workday_email + workday_password,
#      your address, self-ID + screening prefs
# per application:
python main.py apply <id>                  # opens the URL, drives the wizard, pauses at Review
```

### Batch queue (queue N, walk away)

Instead of one at a time, line several up and run them unattended — each fills the
whole wizard and **stops at Review; nothing is submitted**, with a human-like pause
between jobs.

```bash
python main.py queue add <id> <id> ...     # or click "+queue" in the dashboard
python main.py queue list                  # see each job's state
python main.py queue run                   # host only: grind the queue, each stops at Review
```

Then open the dashboard's **Queue & Review** tab, check each application, and click
Submit yourself. `needs_human` = flagged for you; `error` = see the detail column.
When a queue run leaves a fill report, each job also shows a short summary next to its
state (e.g. "1 flagged, 2 errors") so you know at a glance which ones need attention.

**Safety cap.** To avoid flagging an employer you might actually want to work for,
the tool refuses to auto-apply to the *same company* more than 3 times (counts both
`apply` and the queue). Check or clear it:

```bash
python main.py attempts                     # show per-company counts
python main.py attempts reset "Citi"        # clear one company (or omit for all)
```
(Change the limit in `jobagent/attempts.py` → `CAP`.)

The answer bank (`jobagent/workday/answer_bank.py`) pulls name/contact/work
history/education from `profile.json`; `workday_answers.json` adds the Workday-only
fields (login creds, address, LinkedIn/GitHub, "how did you hear", voluntary
self-ID, screening answers, optional structured education). Per-employer screening
varies — run the first one on a new employer with the browser visible and watch;
it logs every field it sets and skips so you can see what to finish by hand.
**This must run on your host, not the container** (it needs your browser + logins).

## Tailoring loop in detail

1. `python main.py tailor <id>` prints a self-contained brief (job + your resume
   + the rules) and saves it to `output/tailored/<id>_brief.txt`.
2. Paste it into a Claude Code session. Claude returns tailored resume text.
3. Save that text to `output/tailored/<id>_draft.txt`.
4. `python main.py render <id>` checks it:
   - **ERROR** (blocks saving): a number/date/metric was added, changed, or dropped.
   - **warn** (asks to confirm): a new capitalized term appeared (possible
     invented employer/tool) — confirm it's legit.
   - Shows a word-level diff. On pass, writes `resume.docx`.
   Use `--force` to override errors (not recommended) or `--yes` to skip warning prompts.

## Configuration

`config.json`:
- `targets` — your titles, locations, remote pref, work auth, salary floor, keywords.
- `sources.greenhouse_boards` / `lever_boards` — company slugs to scan
  (e.g. `stripe` → `boards.greenhouse.io/stripe`).
- `sources.workday_sites` — company Workday careers URLs (`…myworkdayjobs.com/…`).
- `scoring` — weights and `min_score_to_show`.

## Layout

```
main.py            CLI            dashboard.py    Flask view (apply + status)
jobagent/
  config.py  models.py  database.py  profile.py  scorer.py
  tailor.py  docs.py  tracker.py
  sources/   greenhouse.py  lever.py  workday.py  base.py
input/   your resume        output/tailored/   generated docs
data/jobs.db                 tests/             pytest suite
```

## Notes & limits

- Resume parsing is approximate; always review `profile.json`.
- All personal data (resume, profile, DB, outputs) is git-ignored and stays local.
