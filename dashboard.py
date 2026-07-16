"""Local job-search cockpit: ranked jobs + one-click apply + status tracking.

Run: python dashboard.py  ->  http://127.0.0.1:5000

Clicking "Apply" launches the autofiller on your machine — it opens a browser,
fills the application, and pauses for you to review and submit. Local-only
(127.0.0.1); it runs the same `python main.py apply` you'd run by hand.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, redirect, render_template_string, request

from jobagent import applyqueue, attempts, config, database, facts, fillreport, matchcoach, qbank, salary, smartanswer, summarize, tailor
from jobagent.models import ResumeProfile
from jobagent.scorer import location_ok, qualified

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - best effort
        pass

MAIN_PY = Path(__file__).resolve().parent / "main.py"
app = Flask(__name__)
database.init_db()  # idempotent; tolerate an empty/absent DB however we're launched


def _cli_command(*args: str) -> list[str]:
    """Build the subprocess argv to run a `python main.py <args>` CLI command.

    Frozen: sys.executable IS this app's own exe, not a python.exe -- there's
    no separate main.py file to point at either (a frozen build's __file__
    doesn't resolve to a real on-disk script, see config.py:_detect_root).
    app.py's own entry point dispatches argv itself when frozen, so just pass
    the CLI args straight to the exe. Dev: invoke python.exe + main.py as
    normal.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, str(MAIN_PY), *args]


@app.template_filter("safe_url")
def safe_url(value):
    """Allow only http(s) hrefs; anything else (javascript:, data:, …) -> ''."""
    try:
        return value if urlparse(value or "").scheme in ("http", "https") else ""
    except Exception:  # noqa: BLE001 - never let a bad value break the page
        return ""


@app.template_filter("blurb")
def blurb(text):
    """A short, readable snippet of the job description for the list."""
    t = re.sub(r"\s+", " ", summarize.clean_text(text or "")).strip()
    return (t[:200] + "…") if len(t) > 200 else t


@app.template_filter("localtime")
def localtime(ts):
    """SQLite's datetime('now') strings are UTC — show them in the user's local time
    (the queue page's Updated column looked hours off). Non-timestamps pass through."""
    try:
        dt = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ts or ""


app.add_template_filter(summarize.pretty_company, "company")
app.add_template_filter(summarize.company_blurb, "cblurb")
app.add_template_filter(summarize.format_blocks, "blocks")
app.add_template_filter(summarize.match_breakdown, "breakdown")


PAGE = """
<!doctype html><html><head><title>Job Search</title>
<style>
 body{font-family:system-ui,Arial;margin:2rem;background:#f7f7f9;color:#222}
 h1{margin-bottom:.2rem} .sub{color:#666;margin-top:0}
 table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 3px #0002}
 th,td{padding:.5rem .6rem;border-bottom:1px solid #eee;text-align:left;font-size:14px}
 th{background:#305496;color:#fff}
 .score{font-weight:700;vertical-align:top} .reasons{color:#666;font-size:12px}
 .pct{white-space:nowrap}
 .mm{font-weight:400;font-size:11px;line-height:1.3;max-width:15rem;margin-top:.15rem}
 .mm.ok{color:#0b8043}.mm.no{color:#888}.mm.flag{color:#b8860b}
 summary{cursor:pointer;color:#666;font-size:12px;list-style:revert}
 summary:hover{color:#1a73e8}
 .full{font-size:13px;color:#333;margin-top:.5rem;
       max-height:26rem;overflow:auto;padding:.5rem;background:#fafafa;border:1px solid #eee;border-radius:4px}
 .full .jh{font-weight:700;margin:.6rem 0 .2rem;color:#222}
 .full .bl{margin:.1rem 0 .1rem 1rem;text-indent:-1rem}
 .full .pp{margin:.35rem 0}
 .ai{margin:.4rem 0;padding:.4rem .5rem;background:#eef4ff;border-left:3px solid #1a73e8;
     border-radius:4px;font-size:13px;color:#222}
 .co{display:inline}.co summary{color:#1a73e8;font-size:11px}
 .cod{font-size:12px;color:#555;margin-top:.2rem;max-width:22rem}
 .q{display:inline-block;margin-top:.25rem;padding:.1rem .45rem;border-radius:3px;
    font-size:11px;font-weight:700;text-transform:uppercase;color:#fff}
 .q-queued{background:#888}.q-running{background:#1a73e8}.q-filled{background:#0b8043}
 .q-needs_human{background:#b8860b}.q-error{background:#c5221f}
 .s-found{color:#888}.s-tailored{color:#b8860b}.s-applied{color:#1a73e8}
 .s-interview{color:#188038;font-weight:700}.s-offer{color:#0b8043;font-weight:700}
 .s-rejected{color:#c5221f}
 a{color:#1a73e8;text-decoration:none}
 button{cursor:pointer;border:1px solid #ccc;border-radius:4px;padding:.25rem .5rem;background:#fff}
 button.apply{background:#1a73e8;color:#fff;border-color:#1a73e8}
 form{display:inline;margin:0}
</style></head><body>
<h1>Job Search · <a href="/queue" style="font-size:1rem">Queue &amp; Review →</a>
 · <a href="/answers" style="font-size:1rem">Answers →{% if npending %}
 <span style="background:#b8860b;color:#fff;border-radius:9px;padding:.05rem .45rem;
 font-size:12px;font-weight:700">{{ npending }} need you</span>{% endif %}</a>
 · <a href="/profile" style="font-size:1rem">My Info →</a></h1>
{% if stale %}<p class="sub" style="color:#c5221f">That click didn't do anything —
 the listing isn't in the database anymore (probably pruned by a scan since this
 page loaded). Refresh to see the current list.</p>{% endif %}
<p class="sub">{{rows|length}} listings · sorted by fit ·
 {% if show_all %}<a href="/">show only my locations</a>{% else %}<a href="/?all=1">show all locations</a>{% endif %}
 · <b>Apply</b> = autofill now · <b>+queue</b> = add to the batch to run unattended later</p>
<table><tr><th>Status</th><th>Match</th><th>Title</th><th>Company</th>
<th>Location</th><th>Source</th><th>Link</th><th>Apply</th></tr>
{% for r in rows %}
<tr>
 <td class="s-{{r['status']}}">{{r['status']}}
   {% if r['queue_state'] %}<div class="q q-{{r['queue_state']}}">queue: {{r['queue_state']}}</div>{% endif %}
 </td>
 <td class="score">
   <div class="pct">{{ '%.0f'|format(r['score'] or 0) }}%</div>
   {% set mb = r['score_reasons']|breakdown %}
   {% if mb['matched'] %}<div class="mm ok" title="your skills/keywords this posting mentions">&#10003; {{ mb['matched']|join(', ') }}</div>{% endif %}
   {% if mb['missing'] %}<div class="mm no" title="skills of yours this posting doesn't mention (not a gap you need to fill)">&#8211; {{ mb['missing']|join(', ') }}</div>
   <div class="mm"><a href="/coach/{{r['id']}}" style="font-size:11px">&#128161; suggest rewrites</a></div>{% endif %}
   {% for n in mb['notes'] %}<div class="mm flag">&#9888; {{ n }}</div>{% endfor %}
 </td>
 <td>{{r['title']}}
   {% if r['description'] %}
   <details>
     <summary>{{ r['summary'] or r['description']|blurb }}</summary>
     {% if r['summary'] %}<div class="ai"><b>AI summary:</b> {{r['summary']}}</div>{% endif %}
     <div class="full">
       {% for kind, txt in r['description']|blocks %}
         {% if kind == 'head' %}<div class="jh">{{txt}}</div>
         {% elif kind == 'bullet' %}<div class="bl">• {{txt}}</div>
         {% else %}<p class="pp">{{txt}}</p>{% endif %}
       {% endfor %}
     </div>
   </details>
   {% endif %}
 </td>
 <td>{{r['company']|company}}
   {% set cb = r['company']|cblurb %}
   {% if cb %}<details class="co"><summary>about</summary><div class="cod">{{cb}}</div></details>{% endif %}
 </td>
 <td>{{r['location']}}</td><td>{{r['source']}}</td>
 <td>{% set u = r['url']|safe_url %}{% if u %}<a href="{{u}}" target="_blank" rel="noopener noreferrer">open</a>{% endif %}</td>
 <td>
   <form method="post" action="/apply/{{r['id']}}"><button class="apply" type="submit">Apply</button></form>
   <form method="post" action="/queue/add/{{r['id']}}"><button type="submit" title="add to batch queue">+queue</button></form>
   <form method="post" action="/status/{{r['id']}}/applied"><button type="submit" title="mark as applied">✓</button></form>
 </td>
</tr>
{% endfor %}
</table>
{% if not rows %}<p style="background:#fff;padding:1rem;box-shadow:0 1px 3px #0002">
 Nothing to show.{% if not show_all %} Your location/title filters may be hiding
 everything — try <a href="/?all=1">show all locations</a>.{% endif %}
 If the list is empty even then, fetch jobs first: run <code>python main.py scan</code>.</p>
{% endif %}
</body></html>
"""

OPENING = """<!doctype html><meta http-equiv="refresh" content="3;url=/">
<body style="font-family:system-ui;margin:2rem">
Opening the application in a new browser window — review it there and submit.
Heads up: the browser stays open until you close it.
<p><a href="/">← back to the list</a></body>"""

# Shown instead of OPENING when the click could NOT open a browser because the
# company hit the per-company safety cap. Without this the route showed the
# "Opening a browser…" message unconditionally, so a capped click looked like a
# launch that silently vanished (the real "cap reached" message went only to the
# invisible background process). See attempts.py for the cap.
CAPPED = """<!doctype html><body style="font-family:system-ui;margin:2rem;max-width:640px;line-height:1.5">
<h2 style="color:#b8860b">Nothing opened — this employer hit the safety cap</h2>
<p>The tool has already auto-applied to <b>{{ company }}</b> {{ cap }} times. To keep you
from looking like a bot at a company you might actually want to work for, it won't
auto-apply again on its own. <b>That's why no browser appeared</b> — there was nothing to open.</p>
<p>If you're just testing and want to run it on this company again, reset its counter:</p>
<form method="post" action="/attempts/reset/{{ lid }}">
  <button type="submit" style="background:#1a73e8;color:#fff;border:0;padding:.55rem 1rem;
   border-radius:6px;cursor:pointer;font-size:14px">Reset {{ company }} — let me apply again</button>
</form>
<p style="margin-top:1.2rem"><a href="/">← back to the list</a></p></body>"""

QUEUE_PAGE = """
<!doctype html><html><head><title>Apply Queue</title>
{% if active %}<meta http-equiv="refresh" content="4">{% endif %}
<style>
 body{font-family:system-ui,Arial;margin:2rem;background:#f7f7f9;color:#222}
 table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 3px #0002}
 th,td{padding:.5rem .6rem;border-bottom:1px solid #eee;text-align:left;font-size:14px}
 th{background:#305496;color:#fff}
 a{color:#1a73e8;text-decoration:none}
 code{background:#eee;padding:.1rem .3rem;border-radius:3px}
 .st{font-weight:700;text-transform:uppercase;font-size:12px}
 .queued{color:#888}.running{color:#1a73e8}.filled{color:#0b8043}
 .needs_human{color:#b8860b}.error{color:#c5221f}
 .report{display:block;color:#666;font-size:12px}
</style></head><body>
<h1>Apply Queue · <a href="/" style="font-size:1rem">← back to jobs</a></h1>
<p class="sub">Click <b>Run queue</b> to grind every queued job unattended — each opens a
 browser, fills the whole wizard, and <b>stops at Review; nothing is submitted</b>.
 Then open each below and click Submit yourself.
 <b>needs_human</b> = flagged for you; <b>error</b> = see detail.</p>
{% if not rows %}<p>Queue is empty. Add jobs with the <b>+queue</b> button on the job list.</p>{% endif %}
{% if rows %}
<p style="font-size:15px"><b>{{done}} of {{total}} done.</b>
 {% if active %}<span style="color:#1a73e8">working&hellip; this page refreshes itself every few seconds.</span>
 {% else %}<span style="color:#0b8043">queue idle.</span>{% endif %}</p>
<form method="post" action="/queue/run" style="display:inline-block;margin:0 .5rem 1rem 0">
 <button type="submit" style="background:#1a73e8;color:#fff;border:0;padding:.5rem 1rem;border-radius:5px;font-size:14px;cursor:pointer">&#9654; Run queue now</button></form>
<form method="post" action="/queue/reset" style="display:inline-block;margin:0 0 1rem"
 title="force any job stuck on 'running' back so the queue can run again">
 <button type="submit" style="background:#fff;color:#c5221f;border:1px solid #c5221f;padding:.5rem 1rem;border-radius:5px;font-size:14px;cursor:pointer">Reset stuck jobs</button></form>
<p style="color:#666;font-size:13px;margin-top:0">Browsers open one at a time, paced; leave them to it. A stuck job auto-clears after 15 min, or hit <b>Reset stuck jobs</b> now.</p>{% endif %}
{% if rows %}<table><tr><th>Queue</th><th>Status</th><th>Title</th><th>Company</th><th>Detail</th><th>Updated</th><th>Open</th></tr>
{% for r in rows %}
<tr>
 <td class="st {{r['state']}}">{{r['state']}}</td>
 <td>{{r['app_status'] or 'found'}}</td>
 <td>{{r['title'] or r['listing_id']}}</td>
 <td>{{r['company']|company if r['company'] else ''}}</td>
 <td>{{r['detail'] or '' }}{% if r['report'] %}<span class="report">{{r['report']}}</span>{% endif %}</td>
 <td>{{ r['updated_at']|localtime }}</td>
 <td>{% set u = r['url']|safe_url %}{% if u %}<a href="{{u}}" target="_blank" rel="noopener noreferrer">open</a>{% endif %}</td>
</tr>
{% endfor %}
</table>{% endif %}
</body></html>"""


ANSWERS_PAGE = """
<!doctype html><html><head><title>Screening Answers</title>
<style>
 body{font-family:system-ui,Arial;margin:2rem;background:#f7f7f9;color:#222;max-width:52rem}
 h1{margin-bottom:.2rem} .sub{color:#666;margin-top:0}
 a{color:#1a73e8;text-decoration:none}
 .row{background:#fff;box-shadow:0 1px 3px #0002;border-radius:6px;padding:.7rem .9rem;margin:.5rem 0}
 .row.pending{border-left:4px solid #b8860b}
 .q{font-size:14px;font-weight:600;margin-bottom:.35rem}
 .flag{color:#b8860b;font-size:11px;font-weight:700;text-transform:uppercase;margin-left:.4rem}
 input[type=text]{width:100%;box-sizing:border-box;padding:.4rem .5rem;font-size:14px;
   border:1px solid #ccc;border-radius:4px}
 .save{position:sticky;bottom:0;padding:1rem 0}
 .save button{background:#1a73e8;color:#fff;border:0;padding:.6rem 1.4rem;border-radius:5px;
   font-size:15px;cursor:pointer}
 .saved{color:#0b8043;font-size:13px;font-weight:700;margin-left:.6rem}
 .add{margin-top:1rem;color:#666;font-size:13px}
</style></head><body>
<h1>Screening Answers · <a href="/" style="font-size:1rem">← back to jobs</a></h1>
<p class="sub">Answers the autofiller remembers for the yes/no &amp; short questions employers
 ask. Fill a <b style="color:#b8860b">needs answer</b> once and it's reused everywhere.
 Age &amp; work-authorization are already answered from your profile, so they won't show here.</p>
{% if not items %}<p>Nothing parked yet. Questions the tool can't answer during an Apply run
 land here automatically for you to answer once.</p>{% endif %}
<form method="post" action="/answers">
{% for q, a, kind in items %}
 <div class="row {{ 'pending' if (not a and kind not in ('auto','grounded')) else '' }}">
  <div class="q">{{ q }}
   {% if kind == 'auto' %}<span class="flag" style="color:#0b8043">auto-answered</span>
   {% elif kind == 'grounded' %}<span class="flag" style="color:#0b8043">auto-answered from your info</span>
   {% elif not a %}<span class="flag">needs answer</span>{% endif %}</div>
  <input type="hidden" name="q" value="{{ q }}">
  {% if kind == 'auto' %}
   <input type="hidden" name="a" value="{{ a }}">
   <div style="font-size:12px;color:#666">Answered per job from your home location vs. the
    job's location (near → Yes, far → No). Nothing to enter.</div>
  {% elif kind == 'grounded' %}
   <input type="hidden" name="a" value="">
   <div style="font-size:12px;color:#0b8043">Answers <b>{{ a }}</b> automatically from your
    résumé / My Info — nothing to enter. (Change the underlying fact on the My Info page.)</div>
  {% elif kind == 'salary' %}
   <select name="a" style="padding:.4rem .5rem;font-size:14px;border:1px solid #ccc;border-radius:4px">
    <option value="" {{ 'selected' if not a else '' }}>— choose —</option>
    {% for opt, desc in [('below','the bottom'),('average','the middle'),('above','the top')] %}
    <option value="{{opt}}" {{ 'selected' if a==opt else '' }}>{{opt}} — {{desc}} of the job's posted pay range</option>
    {% endfor %}
   </select>
   <div style="font-size:12px;color:#666;margin-top:.3rem">Picks a number from each job's
    posted range automatically (e.g. $80k–$100k: below=80k, average=90k, above=100k). If a
    job posts no range, you'll be asked for a number.</div>
  {% else %}
   <input type="text" name="a" value="{{ a }}" placeholder="your answer (e.g. No)" autocomplete="off">
  {% endif %}
 </div>
{% endfor %}
 <div class="row">
  <div class="q">Add a question you know you'll be asked</div>
  <input type="hidden" name="q" value="">
  <input type="text" name="newq" placeholder="question (leave blank to skip)" autocomplete="off"
   style="margin-bottom:.4rem">
  <input type="text" name="newa" placeholder="answer" autocomplete="off">
 </div>
 <div class="save"><button type="submit">Save answers</button>
  {% if saved %}<span class="saved">&#10003; saved</span>{% endif %}</div>
</form>
</body></html>"""


PROFILE_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>My Info</title>
<style>
 body{font-family:system-ui,Arial;margin:2rem;background:#f7f7f9;color:#222;max-width:60rem}
 h1{margin-bottom:.2rem} h2{margin:1.5rem 0 .2rem;font-size:1.05rem} .sub{color:#666;margin-top:0}
 a{color:#1a73e8;text-decoration:none}
 .card{background:#fff;box-shadow:0 1px 3px #0002;border-radius:6px;padding:1rem 1.1rem;margin:.7rem 0}
 label{display:block;font-weight:600;font-size:14px;margin-bottom:.25rem}
 .help{color:#666;font-size:12px;margin:.15rem 0 .55rem}
 input[type=text],textarea{width:100%;box-sizing:border-box;padding:.4rem .5rem;font-size:14px;
   border:1px solid #ccc;border-radius:4px}
 textarea{min-height:6rem;font-family:inherit}
 .grid{display:grid;grid-template-columns:1fr 1.3fr .7fr 1.1fr 1fr;gap:.4rem;margin:.3rem 0}
 .grid.gov{grid-template-columns:1fr 1.4fr 1.4fr}
 .chk{font-weight:600;font-size:14px} .chk input{margin-right:.4rem;transform:scale(1.15)}
 .save{position:sticky;bottom:0;padding:1rem 0;background:#f7f7f9}
 .save button{background:#1a73e8;color:#fff;border:0;padding:.6rem 1.6rem;border-radius:5px;font-size:15px;cursor:pointer}
 .saved{color:#0b8043;font-size:13px;font-weight:700;margin-left:.6rem}
 .addrow{background:none;border:0;color:#1a73e8;cursor:pointer;font-size:13px;font-weight:600;padding:.2rem 0}
</style></head><body>
<h1>My Info · <a href="/" style="font-size:1rem">&larr; back to jobs</a></h1>
<p class="sub">Fill these once. The autofiller uses them to answer more screening questions
 <b>from facts you gave</b> &mdash; never guessing. Anything left blank is still parked for you.
 This stays on your computer only.</p>
<form method="post" action="/profile">

 <div class="card">
  <label>Highest level of education completed</label>
  <input type="text" name="highest_education" list="edu" value="{{ edu }}" autocomplete="off"
   placeholder="e.g. Bachelor's Degree">
  <datalist id="edu"><option value="High School Diploma"><option value="Associate's Degree">
   <option value="Bachelor's Degree"><option value="Master's Degree"><option value="MBA">
   <option value="Doctorate (PhD)"></datalist>
 </div>

 <div class="card">
  <label>Home country (country of residence)</label>
  <input type="text" name="home_country" value="{{ home_country }}"
   placeholder="United States of America" autocomplete="off">
 </div>

 <div class="card">
  <h2>Every place you've ever worked</h2>
  <div class="help">Started from your resume &mdash; <b>add any it's missing</b> so this is your
   <b>complete</b> history (one per line). This lets "Have you ever worked at X?" answer itself.</div>
  <textarea name="employers" placeholder="Goldman Sachs&#10;Morgan Stanley&#10;...">{{ employers }}</textarea>
  <label class="chk" style="margin-top:.6rem"><input type="checkbox" name="employment_history_complete"
   {{ 'checked' if emp_complete else '' }}>This is my complete work history</label>
  <div class="help">Only when checked will the tool answer "No" to "have you ever worked at X?"
   &mdash; otherwise it asks you, so it never wrongly claims you didn't work somewhere.</div>
 </div>

 <div class="card">
  <h2>Public-company insiders</h2>
  <div class="help">An <b>insider</b> is an officer, director, or significant owner of a
   <b>publicly traded</b> company &mdash; <b>you or a relative</b> (finance applications ask this
   for conflict checks). The ownership threshold varies by employer &mdash; commonly <b>5%+</b>
   (SEC "significant holder") or <b>10%+</b> (Section 16) &mdash; so list any holding at that
   level, and include the <b>amount</b> (shares or %), which the disclosure normally requires.
   Most people have none &mdash; leave blank if so.</div>
  {% for r in insiders %}
  <div class="grid">
   <input type="text" name="insider_who" value="{{ r.get('who','') }}" placeholder="you / relation">
   <input type="text" name="insider_company" value="{{ r.get('company','') }}" placeholder="public company">
   <input type="text" name="insider_ticker" value="{{ r.get('ticker','') }}" placeholder="ticker (opt.)">
   <input type="text" name="insider_role" value="{{ r.get('role','') }}" placeholder="role (director…)">
   <input type="text" name="insider_amount" value="{{ r.get('amount','') }}" placeholder="amount (5% / 12% / 5,000 sh)">
  </div>
  {% endfor %}
  <button type="button" class="addrow" onclick="addRow(this)">+ add another insider</button>
 </div>

 <div class="card">
  <h2>Relatives who work at a company you might apply to</h2>
  <div class="help">Employers ask "do you have relatives working here?" for conflict checks.
   List any relative or close relationship who works at a company you may apply to (any level).
   Leave blank if none &mdash; then the tool answers "No" to these; list one and it parks so you
   handle it.</div>
  {% for r in emprel %}
  <div class="grid gov">
   <input type="text" name="emprel_who" value="{{ r.get('who','') }}" placeholder="relation (e.g. cousin)">
   <input type="text" name="emprel_company" value="{{ r.get('company','') }}" placeholder="company they work at">
   <input type="text" name="emprel_role" value="{{ r.get('role','') }}" placeholder="their role (opt.)">
  </div>
  {% endfor %}
  <button type="button" class="addrow" onclick="addRow(this)">+ add another relative</button>
 </div>

 <div class="card">
  <h2>Professional &amp; securities licenses</h2>
  <div class="help">Every license you hold, comma-separated (blank if none): FINRA exams like
   <b>Series 6</b> / <b>Series 63</b>, the <b>SIE</b>, and non-FINRA ones like a
   <b>Health &amp; Life Insurance Producer</b> license. The tool answers "Yes" to "do you hold
   FINRA licenses?" (only for the FINRA ones) and ticks the matching boxes when a form lists them.</div>
  <input type="text" name="finra_licenses" value="{{ finra }}" autocomplete="off"
   placeholder="e.g. Series 6, Series 63, SIE, Health and Life Insurance Producer">
 </div>

 <div class="card">
  <h2>Other professional disclosures</h2>
  <div class="help">List anything that applies, one per line (blank = none): <b>outside
   business activities</b>, <b>intellectual property</b> you'd retain, or significant
   <b>private-company investments</b>. Leave blank and the tool answers "No" to these; list
   one and it parks so you handle it.</div>
  <textarea name="professional_disclosures" placeholder="(blank if none)">{{ disclosures }}</textarea>
 </div>

 <div class="card">
  <h2>Government / public officials</h2>
  <div class="help">You or a relative who is a government or public official (a "PEP" check).
   Most people have none.</div>
  {% for r in gov %}
  <div class="grid gov">
   <input type="text" name="gov_who" value="{{ r.get('who','') }}" placeholder="you / relation">
   <input type="text" name="gov_role" value="{{ r.get('role','') }}" placeholder="role / office">
   <input type="text" name="gov_entity" value="{{ r.get('entity','') }}" placeholder="agency / entity">
  </div>
  {% endfor %}
  <button type="button" class="addrow" onclick="addRow(this)">+ add another official</button>
  <label class="chk" style="margin-top:.7rem"><input type="checkbox" name="related_party_complete"
   {{ 'checked' if rp_complete else '' }}>I've listed everything above &mdash; all my (and my
   family's) public-company insider positions, relatives working at a company I might apply to,
   government-official roles, and professional disclosures (licenses, outside business, IP,
   investments)</label>
  <div class="help">Only when checked will the tool answer "No" to these conflict questions
   &mdash; otherwise it parks them for you.</div>
 </div>

 <div class="save"><button type="submit">Save my info</button>
  {% if saved %}<span class="saved">&#10003; saved</span>{% endif %}</div>
</form>
<script>
// Clone the last blank row in a section so you can list more than the default rows.
// The form parser (facts._rows) already accepts any number of rows.
function addRow(btn){
  var card = btn.closest('.card'), grids = card.querySelectorAll('.grid');
  var row = grids[grids.length - 1].cloneNode(true);
  row.querySelectorAll('input').forEach(function(i){ i.value = ''; });
  btn.parentNode.insertBefore(row, btn);
}
</script>
</body></html>"""


COACH_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Rewrite Suggestions</title>
<style>
 body{font-family:system-ui,Arial;margin:2rem;background:#f7f7f9;color:#222;max-width:44rem}
 h1{margin-bottom:.2rem;font-size:1.3rem} a{color:#1a73e8;text-decoration:none}
 .sub{color:#666;font-size:13px;margin-bottom:1.5rem}
 .card{background:#fff;border:1px solid #ddd;border-radius:6px;padding:1rem;margin-bottom:1rem}
 .trait{color:#1a73e8;font-size:11px;font-weight:700;text-transform:uppercase;margin-bottom:.4rem}
 .orig{color:#888;font-size:13px;text-decoration:line-through;margin-bottom:.3rem}
 .new{font-size:14px}
 .caution{color:#b8860b;font-size:12px;margin-top:.5rem}
 .empty{color:#666}
</style></head><body>
<h1>Rewrite Suggestions &middot; <a href="/">&larr; back to jobs</a></h1>
<p class="sub">{{ title }}{% if company %} at {{ company }}{% endif %}. Suggestions only
 &mdash; nothing is saved automatically. Copy anything you want into your own resume
 yourself, and check every caution first.</p>
{% if applied == '1' %}<p style="color:#0b8043">&#10003; Applied to profile.json (the old
 version was saved as profile.json.bak first).</p>{% endif %}
{% if applied == '0' %}<p style="color:#c5221f">Couldn't apply that -- the bullet didn't
 match exactly (resume may have changed since this suggestion was made).</p>{% endif %}
{% if error %}<p class="empty">{{ error }}</p>
{% elif not suggestions %}<p class="empty">No honest rewrite found for this job's unmatched
 traits &mdash; either nothing in your resume genuinely supports them, or the local AI box
 (Ollama) isn't running.</p>
{% else %}
{% for s in suggestions %}
 <div class="card">
  <div class="trait">for: {{ s.for_trait }} &middot; {{ s.company }} / {{ s.title }}</div>
  <div class="orig">{{ s.original }}</div>
  <div class="new">{{ s.suggested }}</div>
  {% for c in s.cautions %}<div class="caution">&#9888; {{ c }}</div>{% endfor %}
  <form method="post" action="/coach/{{ lid }}/apply" style="margin-top:.6rem">
   <input type="hidden" name="company" value="{{ s.company }}">
   <input type="hidden" name="title" value="{{ s.title }}">
   <input type="hidden" name="original" value="{{ s.original }}">
   <input type="hidden" name="suggested" value="{{ s.suggested }}">
   <button type="submit" style="background:#1a73e8;color:#fff;border:0;padding:.4rem .9rem;
    border-radius:4px;font-size:12px;cursor:pointer">Apply to my resume</button>
  </form>
 </div>
{% endfor %}
{% endif %}
</body></html>"""


@app.route("/coach/<lid>")
def coach(lid):
    applied = request.args.get("applied")
    conn = database.connect()
    try:
        row = database.get_listing(conn, lid)
    finally:
        conn.close()
    if row is None:
        return render_template_string(COACH_PAGE, lid=lid, title="that job", company="",
                                      suggestions=[], error="Job not found.", applied=applied)
    if not config.PROFILE_PATH.exists():
        return render_template_string(
            COACH_PAGE, lid=lid, title=row["title"], company=row["company"], suggestions=[],
            error="No profile.json -- run `python main.py setup` first.", applied=applied)
    profile = ResumeProfile.from_dict(json.loads(config.PROFILE_PATH.read_text("utf-8")))
    # The coach computes its own trait list from THIS job's text (what the JD
    # asks for that the resume doesn't say), not the score display's capped
    # 'missing' string — see matchcoach.coach_traits (ISSUES G fix).
    targets = profile.targets or config.load_config().get("targets", {})
    missing = matchcoach.coach_traits(profile, row["description"] or "",
                                      targets.get("keywords", []))
    if not missing:
        return render_template_string(
            COACH_PAGE, lid=lid, title=row["title"], company=row["company"], suggestions=[],
            error="This job's posting doesn't ask for anything your resume is missing "
                  "(among your configured keywords) — nothing to coach toward.", applied=applied)
    suggestions = matchcoach.suggest_all(profile, row["description"], missing)
    return render_template_string(
        COACH_PAGE, lid=lid, title=row["title"], company=row["company"],
        suggestions=suggestions, error=None, applied=applied)


@app.route("/coach/<lid>/apply", methods=["POST"])
def coach_apply(lid):
    company = request.form.get("company", "")
    title = request.form.get("title", "")
    original = request.form.get("original", "")
    suggested = request.form.get("suggested", "")
    ok = False
    if config.PROFILE_PATH.exists():
        raw = config.PROFILE_PATH.read_text("utf-8")
        data = json.loads(raw)
        # Re-check the fact-lock here too, not just at suggestion-generation time --
        # the AI's output never gets the last word, the code always re-derives it,
        # same rule as everywhere else in this codebase (guardrail.py, tailor.py).
        # A POST is just form data; nothing guarantees it still matches what was
        # actually validated when the suggestion was shown.
        profile = ResumeProfile.from_dict(data)
        if tailor.validate(original, suggested, profile).ok:
            ok = matchcoach.apply_bullet(data, company, title, original, suggested)
        if ok:
            backup = config.PROFILE_PATH.with_name(config.PROFILE_PATH.name + ".bak")
            backup.write_text(raw, encoding="utf-8")
            config.PROFILE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return redirect(f"/coach/{lid}?applied={'1' if ok else '0'}")


def _grounded_answers(pending):
    """Of the given parked questions, which ones the honesty guardrail can now answer from
    the résumé + My Info — so they no longer 'need you'. Returns {question: answer}. Dynamic:
    reflects the current logic + facts on every load, so nothing has to be deleted."""
    from jobagent import guardrail
    from jobagent.workday.answer_bank import build_answers
    try:
        ans = build_answers()
    except Exception:  # noqa: BLE001 - no profile yet, etc. -> nothing auto-answers
        return {}
    out = {}
    for q in pending:
        for fn in (guardrail.resolve, guardrail.value_answer, guardrail.ever_worked_answer,
                   guardrail.current_employer_answer, guardrail.related_party_answer,
                   guardrail.disclosure_answer, guardrail.finra_answer):
            r = fn(q, ans)
            if r and r != guardrail.NEEDS_HUMAN:
                out[q] = r
                break
    return out


@app.route("/answers", methods=["GET", "POST"])
def answers():
    if request.method == "POST":
        qs = request.form.getlist("q")
        ans = request.form.getlist("a")
        data = {q.strip(): a.strip() for q, a in zip(qs, ans) if q.strip()}
        newq, newa = request.form.get("newq", "").strip(), request.form.get("newa", "").strip()
        if newq:
            data[newq] = newa
        qbank.save(data)
        return redirect("/answers?saved=1")
    data = qbank.load()
    # pending (blank) first so what needs you is up top; then alphabetical. Each item
    # carries a widget 'kind': 'salary' -> below/avg/above dropdown, 'auto' -> answered
    # per-job by the tool (residence), 'text' -> free entry.
    grounded = _grounded_answers([q for q, a in data.items() if not a])

    def kind(q, a):
        if smartanswer.is_salary_question(q):
            # The strategy dropdown can only show ''/below/average/above. A typed
            # literal (e.g. "85000") must keep a text box — the dropdown would render
            # "— choose —" and the next Save would silently wipe the number.
            if not a or a.strip().lower() in salary.STRATEGIES:
                return "salary"
            return "text"
        if smartanswer.is_residence_question(q):
            return "auto"
        return "text"
    items = []
    for q, a in sorted(data.items(), key=lambda kv: (bool(kv[1]) or kv[0] in grounded, kv[0].lower())):
        if not a and q in grounded:
            items.append((q, grounded[q], "grounded"))   # now answered by the logic — not "needs you"
        else:
            k = kind(q, a)
            if k == "salary" and a:
                a = a.strip().lower()   # 'Average ' still selects its dropdown option
            items.append((q, a, k))
    return render_template_string(ANSWERS_PAGE, items=items,
                                  saved=bool(request.args.get("saved")))


@app.route("/profile", methods=["GET", "POST"])
def profile():
    """My Info: the one-time grounded facts (education, home country, full job history,
    public-company insiders, officials) the honesty guardrail answers from. Saved locally
    to grounded_facts.json — never the credentials file."""
    if request.method == "POST":
        facts.save(facts.parse_profile_form(request.form.get, request.form.getlist))
        return redirect("/profile?saved=1")
    f = facts.load()
    # Pre-fill from the resume so Ethan never re-types facts already on file. His saved
    # answers win; the resume-derived values only fill the still-blank fields.
    from jobagent.workday import answer_bank
    d = answer_bank.profile_defaults()
    pad = lambda rows, n: list(rows or []) + [{}] * n  # trailing blank rows to add more
    return render_template_string(
        PROFILE_PAGE,
        edu=f.get("highest_education") or d.get("highest_education", ""),
        home_country=f.get("home_country") or d.get("home_country", ""),
        employers="\n".join(f.get("employers_all") or d.get("employers_all") or []),
        emp_complete=bool(f.get("employment_history_complete")),
        insiders=pad(f.get("insiders"), 3),
        emprel=pad(f.get("employer_relatives"), 2),
        finra=", ".join(f.get("finra_licenses") or d.get("finra_licenses") or []),
        disclosures="\n".join(f.get("professional_disclosures") or []),
        gov=pad(f.get("government_officials"), 2),
        rp_complete=bool(f.get("related_party_complete")),
        saved=bool(request.args.get("saved")),
    )


@app.route("/")
def index():
    cfg = config.load_config()
    targets = cfg.get("targets", {})
    show_all = request.args.get("all") == "1"
    stale = request.args.get("stale") == "1"
    conn = database.connect()
    try:
        rows = database.ranked_listings(conn, cfg["scoring"]["min_score_to_show"])
    finally:
        conn.close()
    if not show_all:
        rows = [r for r in rows if location_ok(r["location"], bool(r["remote"]), targets)
                and qualified(r["title"], targets)]
    pend = qbank.pending()
    npending = len(pend) - len(_grounded_answers(pend))   # grounded ones no longer 'need you'
    return render_template_string(PAGE, rows=rows, show_all=show_all, stale=stale,
                                  npending=npending)


@app.route("/apply/<lid>", methods=["POST"])
def apply(lid):
    conn = database.connect()
    try:
        row = conn.execute("SELECT company FROM listings WHERE id=?", (lid,)).fetchone()
        # Read-only cap check (does NOT increment — the launched run does the
        # authoritative atomic try_record). Lets us tell the truth up front
        # instead of firing a subprocess that just exits with an unseen message.
        capped = bool(row) and not attempts.allowed(conn, row["company"])
        company = row["company"] if row else ""
    finally:
        conn.close()
    if not row:
        return redirect("/?stale=1")  # listing pruned since the page loaded — don't fake "opening"
    if capped:
        return render_template_string(CAPPED, company=company, lid=lid, cap=attempts.CAP)
    # Launch the same autofiller you'd run by hand; --keep-open so it doesn't
    # wait on a terminal keypress (there isn't one here).
    subprocess.Popen(_cli_command("apply", lid, "--keep-open"), cwd=str(config.ROOT))
    return OPENING


@app.route("/attempts/reset/<lid>", methods=["POST"])
def attempts_reset(lid):
    """Clear the safety cap for this listing's company so a repeat (test) apply can
    run again — the dashboard-button equivalent of `main.py attempts reset "<co>"`."""
    conn = database.connect()
    try:
        row = conn.execute("SELECT company FROM listings WHERE id=?", (lid,)).fetchone()
        if row:
            attempts.reset(conn, row["company"])
    finally:
        conn.close()
    return redirect("/")


@app.route("/queue")
def queue_view():
    conn = database.connect()
    try:
        applyqueue.ensure_table(conn)
        raw = conn.execute(
            "SELECT q.listing_id, q.state, q.detail, q.updated_at, l.title, l.company, l.url, "
            "a.status AS app_status "
            "FROM apply_queue q LEFT JOIN listings l ON l.id = q.listing_id "
            "LEFT JOIN applications a ON a.listing_id = q.listing_id "
            "ORDER BY q.updated_at DESC"
        ).fetchall()
        # Attach a short fill-report summary per row when one exists (queue-run path
        # writes it). Read-only; dicts so the template can add the extra field.
        rows = []
        for r in raw:
            d = dict(r)
            rep = fillreport.get(conn, r["listing_id"])
            if rep:
                d["report"] = (f"{len(rep['flagged'])} flagged, "
                               f"{len(rep['errors'])} errors")
            rows.append(d)
    finally:
        conn.close()
    # Progress line + whether to auto-refresh (only while something is still moving).
    total = len(rows)
    done = sum(1 for r in rows if r["state"] in ("filled", "needs_human", "error"))
    active = any(r["state"] in ("queued", "running") for r in rows)
    return render_template_string(QUEUE_PAGE, rows=rows, total=total, done=done,
                                  active=active)


@app.route("/queue/add/<lid>", methods=["POST"])
def queue_add(lid):
    conn = database.connect()
    try:
        if conn.execute("SELECT 1 FROM listings WHERE id=?", (lid,)).fetchone():
            applyqueue.enqueue(conn, lid)
    finally:
        conn.close()
    return redirect("/queue")


@app.route("/queue/run", methods=["POST"])
def queue_run():
    """Start the batch: grind every queued job unattended, each stops at Review.
    Same detached launch as single Apply. Guard against a double-click starting a
    second grinder over the same jobs (the per-company attempts cap is the backstop,
    this just avoids the obvious race)."""
    conn = database.connect()
    try:
        applyqueue.ensure_table(conn)
        applyqueue.reset_stuck(conn)  # a stale 'running' (crashed run) must not lock the queue
        already_running = any(r["state"] == "running" for r in applyqueue.pending(conn))
        has_queued = applyqueue.next_queued(conn) is not None
    finally:
        conn.close()
    if has_queued and not already_running:
        subprocess.Popen(_cli_command("queue", "run"), cwd=str(config.ROOT))
    return redirect("/queue")


@app.route("/queue/reset", methods=["POST"])
def queue_reset():
    """Manual unstick: force any 'running' job back to error so the queue can run
    again. For when you KNOW a job is dead and don't want to wait out the timeout."""
    conn = database.connect()
    try:
        applyqueue.reset_stuck(conn, minutes=0)
    finally:
        conn.close()
    return redirect("/queue")


@app.route("/status/<lid>/<st>", methods=["POST"])
def set_status(lid, st):
    updated = False
    if st in database.STATUSES:
        conn = database.connect()
        try:
            updated = database.set_status(conn, lid, st)
            conn.commit()
        finally:
            conn.close()
    return redirect("/" if updated else "/?stale=1")


if __name__ == "__main__":
    # threaded=True: /coach/<lid> can take minutes (one Ollama call per bullet,
    # each up to 60s, before it's tried them all) -- without this, Werkzeug's
    # single-threaded dev server blocks the ENTIRE dashboard (job list, Apply,
    # queue, status buttons) for that whole time, looking hung. Found live,
    # 2026-07-09, by an overnight adversarial audit. Safe to enable now: this
    # same audit also closed the real concurrency gaps (qbank/attempts/
    # applyqueue) that concurrent requests would otherwise have exposed.
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
