"""Local job-search cockpit: ranked jobs + one-click apply + status tracking.

Run: python dashboard.py  ->  http://127.0.0.1:5000

Clicking "Apply" launches the autofiller on your machine — it opens a browser,
fills the application, and pauses for you to review and submit. Local-only
(127.0.0.1); it runs the same `python main.py apply` you'd run by hand.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, redirect, render_template_string, request

from jobagent import applyqueue, config, database, fillreport, qbank, summarize
from jobagent.scorer import location_ok, qualified

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - best effort
        pass

MAIN_PY = Path(__file__).resolve().parent / "main.py"
app = Flask(__name__)
database.init_db()  # idempotent; tolerate an empty/absent DB however we're launched


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
 · <a href="/answers" style="font-size:1rem">Answers →</a></h1>
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
   <div class="pct" title="{{r['score_reasons']}}">{{ '%.0f'|format(r['score'] or 0) }}%</div>
   {% set mb = r['score_reasons']|breakdown %}
   {% if mb['matched'] %}<div class="mm ok" title="your skills/keywords this posting mentions">&#10003; {{ mb['matched']|join(', ') }}</div>{% endif %}
   {% if mb['missing'] %}<div class="mm no" title="skills of yours this posting doesn't mention (not a gap you need to fill)">&#8211; {{ mb['missing']|join(', ') }}</div>{% endif %}
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
</table></body></html>
"""

OPENING = """<!doctype html><meta http-equiv="refresh" content="3;url=/">
<body style="font-family:system-ui;margin:2rem">
Opening the application in a new browser window — review it there and submit.
Heads up: the browser stays open until you close it.
<p><a href="/">← back to the list</a></body>"""

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
 <td>{{r['updated_at'] or ''}}</td>
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
 .add{margin-top:1rem;color:#666;font-size:13px}
</style></head><body>
<h1>Screening Answers · <a href="/" style="font-size:1rem">← back to jobs</a></h1>
<p class="sub">Answers the autofiller remembers for the yes/no &amp; short questions employers
 ask. Fill a <b style="color:#b8860b">needs answer</b> once and it's reused everywhere.
 Age &amp; work-authorization are already answered from your profile, so they won't show here.</p>
{% if not items %}<p>Nothing parked yet. Questions the tool can't answer during an Apply run
 land here automatically for you to answer once.</p>{% endif %}
<form method="post" action="/answers">
{% for q, a in items %}
 <div class="row {{ 'pending' if not a else '' }}">
  <div class="q">{{ q }}{% if not a %}<span class="flag">needs answer</span>{% endif %}</div>
  <input type="hidden" name="q" value="{{ q }}">
  <input type="text" name="a" value="{{ a }}" placeholder="your answer (e.g. No)" autocomplete="off">
 </div>
{% endfor %}
 <div class="row">
  <div class="q">Add a question you know you'll be asked</div>
  <input type="hidden" name="q" value="">
  <input type="text" name="newq" placeholder="question (leave blank to skip)" autocomplete="off"
   style="margin-bottom:.4rem">
  <input type="text" name="newa" placeholder="answer" autocomplete="off">
 </div>
 <div class="save"><button type="submit">Save answers</button></div>
</form>
</body></html>"""


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
        return redirect("/answers")
    data = qbank.load()
    # pending (blank) first so what needs you is up top; then alphabetical.
    items = sorted(data.items(), key=lambda kv: (bool(kv[1]), kv[0].lower()))
    return render_template_string(ANSWERS_PAGE, items=items)


@app.route("/")
def index():
    cfg = config.load_config()
    targets = cfg.get("targets", {})
    show_all = request.args.get("all") == "1"
    conn = database.connect()
    try:
        rows = database.ranked_listings(conn, cfg["scoring"]["min_score_to_show"])
    finally:
        conn.close()
    if not show_all:
        rows = [r for r in rows if location_ok(r["location"], bool(r["remote"]), targets)
                and qualified(r["title"])]
    return render_template_string(PAGE, rows=rows, show_all=show_all)


@app.route("/apply/<lid>", methods=["POST"])
def apply(lid):
    conn = database.connect()
    try:
        exists = conn.execute("SELECT 1 FROM listings WHERE id=?", (lid,)).fetchone()
    finally:
        conn.close()
    if exists:
        # Launch the same autofiller you'd run by hand; --keep-open so it doesn't
        # wait on a terminal keypress (there isn't one here).
        subprocess.Popen([sys.executable, str(MAIN_PY), "apply", lid, "--keep-open"],
                         cwd=str(config.ROOT))
    return OPENING


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
        subprocess.Popen([sys.executable, str(MAIN_PY), "queue", "run"],
                         cwd=str(config.ROOT))
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
    if st in database.STATUSES:
        conn = database.connect()
        try:
            database.set_status(conn, lid, st)
            conn.commit()
        finally:
            conn.close()
    return redirect("/")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
