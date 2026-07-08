"""Job-search agent CLI.

Typical flow:
  1. Put your resume in input/  (PDF or DOCX)
  2. python main.py setup            # parse resume + set targets -> profile.json
  3. python main.py scan             # pull jobs from configured sources
  4. python main.py list             # see ranked matches
  5. python main.py tailor <id>      # print brief to paste into Claude Code
  6. python main.py render <id>      # validate Claude's draft + write resume.docx
  7. python main.py status <id> applied
  8. python main.py export           # Excel tracker

Most people just run `python dashboard.py` instead of list/apply by hand.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jobagent import config, database
from jobagent.models import Listing, ResumeProfile
from jobagent.scorer import score_listing, location_ok, qualified
from jobagent import tailor, docs, tracker
from jobagent.sources import greenhouse, lever, workday


def _force_utf8_io() -> None:
    """Make stdout/stderr UTF-8 so printing résumé/JD text never crashes.

    On Windows the default console code page is cp1252; when output is piped or
    redirected, Python encodes with cp1252 + errors='strict', so a single
    non-cp1252 glyph (●, →, ≥ — common in job descriptions) raises
    UnicodeEncodeError and aborts the command. Reconfiguring with
    errors='replace' guarantees no command ever dies on an exotic character.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - older/odd streams: best effort only
            pass


def _load_profile() -> ResumeProfile:
    if not config.PROFILE_PATH.exists():
        sys.exit("No profile.json. Run: python main.py setup")
    return ResumeProfile.from_dict(json.loads(config.PROFILE_PATH.read_text("utf-8")))


def cmd_setup(args):
    config.ensure_dirs()
    if config.PROFILE_PATH.exists() and not args.force:
        sys.exit(
            f"{config.PROFILE_PATH} already exists. Re-parsing will overwrite any "
            "corrections you made. Re-run with --force to overwrite."
        )
    resumes = [p for p in config.INPUT_DIR.iterdir()
               if p.suffix.lower() in (".pdf", ".docx", ".txt", ".md")] \
        if config.INPUT_DIR.exists() else []
    if not resumes:
        sys.exit(f"Drop your resume (PDF/DOCX) in {config.INPUT_DIR} first.")
    from jobagent.profile import parse_resume

    path = resumes[0]
    print(f"Parsing {path.name} ...")
    prof = parse_resume(path)

    cfg = config.load_config()
    prof.targets = cfg.get("targets", {})

    config.PROFILE_PATH.write_text(
        json.dumps(prof.to_dict(), indent=2), encoding="utf-8"
    )
    print(f"Wrote {config.PROFILE_PATH}")
    print(f"  name: {prof.name}")
    print(f"  skills: {len(prof.skills)}  experience items: {len(prof.experience)}")
    print("REVIEW profile.json and fix any parsing mistakes before scanning.")


def _scan_sources(cfg) -> list[Listing]:
    src = cfg["sources"]
    targets = cfg.get("targets", {})
    # One task per board; each does its own network I/O, so run them concurrently.
    # ponytail: 6 boards in flight; Workday boards fan out 8 more internally, so
    # ~48 sockets worst case — fine for a personal scan.
    tasks = (
        [(f"Greenhouse: {b}", greenhouse.fetch, (b,)) for b in src.get("greenhouse_boards", [])]
        + [(f"Lever: {b}", lever.fetch, (b,)) for b in src.get("lever_boards", [])]
        + [(f"Workday: {s}", workday.fetch, (s, targets)) for s in src.get("workday_sites", [])]
    )
    found: list[Listing] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = []
        for label, fn, args in tasks:
            print(label)
            futures.append(pool.submit(fn, *args))
        for fut in futures:
            found.extend(fut.result())
    return found


def _configured_source_keys(cfg) -> set[str]:
    """The `source` values (greenhouse:<b>, lever:<b>, workday:<tenant>) we're set
    up to scan — used to tell a dropped source from one that merely failed a scan."""
    from urllib.parse import urlparse
    src = cfg["sources"]
    keys = {f"greenhouse:{b}" for b in src.get("greenhouse_boards", [])}
    keys |= {f"lever:{b}" for b in src.get("lever_boards", [])}
    for u in src.get("workday_sites", []):
        host = urlparse(u).hostname or ""
        if host:
            keys.add(f"workday:{host.split('.')[0]}")
    return keys


def cmd_scan(args):
    from jobagent.sources.base import now_iso

    config.ensure_dirs()
    database.init_db()
    cfg = config.load_config()
    prof = _load_profile()

    scan_start = now_iso()  # rows not refreshed past this point are stale
    listings = _scan_sources(cfg)
    # dedup by id
    by_id = {l.id: l for l in listings}
    print(f"Fetched {len(listings)} listings ({len(by_id)} unique).")

    conn = database.connect()
    try:
        kept = 0
        for listing in by_id.values():
            score, reasons = score_listing(listing, prof, cfg)
            row = listing.to_dict()
            row["score"] = score
            row["score_reasons"] = reasons
            database.upsert_listing(conn, row)
            kept += 1
        pruned = database.prune_stale_listings(
            conn, scan_start,
            succeeded={l.source for l in listings},
            attempted=_configured_source_keys(cfg),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"Scored and saved {kept}. Pruned {pruned} stale/dead listings. "
          f"Run: python main.py list")


def cmd_list(args):
    cfg = config.load_config()
    targets = cfg.get("targets", {})
    conn = database.connect()
    try:
        rows = database.ranked_listings(conn, cfg["scoring"]["min_score_to_show"])
    finally:
        conn.close()
    if not args.all:
        rows = [r for r in rows if location_ok(r["location"], bool(r["remote"]), targets)
                and qualified(r["title"])]
    if not rows:
        print("No matches. Run `scan` first (or lower min_score_to_show, or --all).")
        return
    for r in rows[: args.limit]:
        print(f"[{r['score']:5}] {r['id']}  {r['status']:9} {r['title']} @ {r['company']}")
        if r["score_reasons"]:
            print(f"          {r['score_reasons']}")


def cmd_summarize(args):
    """Fill the cached AI summary for listings that don't have one yet.

    Off the dashboard's critical path: run it when your local Ollama box is up;
    the dashboard shows whatever's cached and works fine without it.
    """
    from jobagent import summarize
    cfg = config.load_config()
    targets = cfg.get("targets", {})
    conn = database.connect()
    try:
        rows = database.ranked_listings(conn, cfg["scoring"]["min_score_to_show"])
        if not args.all:  # default: only the jobs the dashboard actually shows
            rows = [r for r in rows if location_ok(r["location"], bool(r["remote"]), targets)
                    and qualified(r["title"])]
        todo = [r for r in rows if not (r["summary"] or "").strip()][: args.limit]
        if not todo:
            print("Nothing to summarize (all shown listings already have a summary).")
            return
        print(f"Summarizing {len(todo)} listings via {args.model} … (Ctrl-C to stop; progress is saved)")
        done = 0
        for r in todo:
            s = summarize.ai_summary(r["description"], r["title"],
                                     summarize.pretty_company(r["company"]),
                                     model=args.model, base=args.base)
            if s is None:
                print(f"  ! {r['id']} skipped (Ollama unreachable at {args.base}?)")
                continue
            database.set_summary(conn, r["id"], s)
            conn.commit()
            done += 1
            print(f"  ✓ {r['company']}: {r['title'][:50]}")
        print(f"Done. {done} summarized.")
    finally:
        conn.close()


def _get_listing(conn, listing_id) -> Listing:
    row = conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
    if not row:
        sys.exit(f"No listing {listing_id}")
    return Listing(
        title=row["title"], company=row["company"], description=row["description"],
        url=row["url"], location=row["location"], remote=bool(row["remote"]),
        salary=row["salary"], source=row["source"], posted_date=row["posted_date"],
        fetched_at=row["fetched_at"],
    )


def cmd_tailor(args):
    prof = _load_profile()
    conn = database.connect()
    try:
        listing = _get_listing(conn, args.id)
    finally:
        conn.close()
    brief = tailor.build_brief(prof, listing)
    brief_path = config.TAILORED_DIR / f"{args.id}_brief.txt"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(brief, encoding="utf-8")
    print("=" * 70)
    print(brief)
    print("=" * 70)
    print(f"\nBrief saved to {brief_path}")
    print("Paste the above into a Claude Code session. Save Claude's tailored")
    print(f"resume text to: {config.TAILORED_DIR / (args.id + '_draft.txt')}")
    print(f"Then run: python main.py render {args.id}")


def cmd_render(args):
    prof = _load_profile()
    draft_path = config.TAILORED_DIR / f"{args.id}_draft.txt"
    if not draft_path.exists():
        sys.exit(f"Expected Claude's draft at {draft_path}")
    tailored = draft_path.read_text("utf-8")

    # Resolve the listing (and its current status) before any heavy work, so a
    # mistyped id fails fast.
    conn = database.connect()
    try:
        listing = _get_listing(conn, args.id)
        row = conn.execute(
            "SELECT status FROM applications WHERE listing_id=?", (args.id,)
        ).fetchone()
        current_status = row["status"] if row else "found"
    finally:
        conn.close()

    # Validate against the master resume facts.
    result = tailor.validate(prof.raw_text, tailored, prof)
    print(result.report())
    print("\n--- DIFF (review carefully) ---")
    print(result.diff or "(no line-level changes detected)")

    if not result.ok and not args.force:
        sys.exit("\nValidation FAILED. Fix the draft, or re-run with --force to override.")
    if result.warnings and not args.yes:
        ans = input("\nWarnings present. Render anyway? [y/N] ").strip().lower()
        if ans != "y":
            sys.exit("Aborted.")

    paths = docs.render(listing.company, listing.title, tailored)
    # Advance 'found' -> 'tailored', but never move an already-advanced
    # application (applied/interview/offer) backward when re-rendering.
    new_status = "tailored" if current_status == "found" else current_status
    conn = database.connect()
    try:
        database.set_status(conn, args.id, new_status, doc_path=paths["resume_docx"])
        conn.commit()
    finally:
        conn.close()
    print("\nWrote:")
    for k, v in paths.items():
        print(f"  {k}: {v}")


def cmd_status(args):
    new_status = args.new_status.strip().lower()
    if new_status not in database.STATUSES:
        sys.exit(f"Unknown status {args.new_status!r}. Choose one of: "
                 + ", ".join(database.STATUSES))
    conn = database.connect()
    try:
        _get_listing(conn, args.id)  # exits with a clear message if id is unknown
        database.set_status(conn, args.id, new_status,
                            follow_up=args.follow_up, notes=args.notes)
        conn.commit()
    finally:
        conn.close()
    print(f"{args.id} -> {new_status}")


def cmd_export(args):
    path = tracker.export_xlsx()
    print(f"Wrote {path}")


def cmd_doctor(args):
    """Preflight: verify the environment is ready (run this first on a new host)."""
    ok = "[ OK ]"
    bad = "[FAIL]"
    warn = "[warn]"
    print(f"Python {sys.version.split()[0]}")

    enc = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
    if "utf8" in enc:
        print(f"{ok} console encoding {sys.stdout.encoding}")
    else:
        print(f"{warn} console encoding {sys.stdout.encoding} (non-UTF-8; run via "
              "the .bat launchers or set PYTHONUTF8=1 so résumé/JD glyphs don't break output)")

    core = ["requests", "dotenv", "pdfplumber", "docx", "openpyxl", "flask"]
    for mod in core:
        try:
            __import__(mod)
            print(f"{ok} import {mod}")
        except ImportError:
            print(f"{bad} import {mod}  ->  pip install -r requirements.txt")

    try:
        import playwright  # noqa: F401
        print(f"{ok} playwright (Workday autofill ready)")
    except ImportError:
        print(f"{warn} playwright not installed (only needed for `apply`): "
              "pip install playwright && playwright install chromium")

    # Data files
    if config.PROFILE_PATH.exists():
        try:
            prof = _load_profile()
            print(f"{ok} profile.json ({len(prof.experience)} roles, {len(prof.skills)} skills)")
        except Exception as exc:  # noqa: BLE001
            print(f"{bad} profile.json invalid: {exc}")
    else:
        print(f"{bad} profile.json missing  ->  put resume in input/ then `python main.py setup`")

    resumes = [p.name for p in config.INPUT_DIR.glob("*.pdf")] + \
              [p.name for p in config.INPUT_DIR.glob("*.docx")] if config.INPUT_DIR.exists() else []
    print(f"{ok if resumes else warn} resume in input/: {resumes or '(none)'}")

    print(f"{ok if config.CONFIG_PATH.exists() else warn} config.json "
          f"{'present' if config.CONFIG_PATH.exists() else 'missing (using defaults)'}")

    if config.DB_PATH.exists():
        conn = database.connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        finally:
            conn.close()
        print(f"{ok} jobs.db ({n} listings scanned)")
    else:
        print(f"{warn} no jobs.db yet  ->  `python main.py scan`")

    wa = config.ROOT / "workday_answers.json"
    print(f"{ok if wa.exists() else warn} workday_answers.json "
          + ("present" if wa.exists() else
             "missing -> run `python main.py workday-init`, then copy "
             "workday_answers.example.json to workday_answers.json"))


def cmd_workday_init(args):
    from jobagent.workday import answer_bank

    tmpl = answer_bank.save_template()
    print(f"Wrote {tmpl}")
    print("Copy it to workday_answers.json and fill in self-ID / screening prefs.")
    print("Resume + work history are pulled from profile.json automatically.")
    ans = answer_bank.build_answers()
    print(f"\nAnswer bank ready: {len(ans['experience'])} jobs, "
          f"resume={ans['resume_file'] or '(none found in input/)'}")


def cmd_apply(args):
    import os
    from jobagent import applier, attempts

    if getattr(args, "trace", False):
        os.environ["JOBAGENT_TRACE"] = "1"  # record without relying on shell state

    conn = database.connect()
    try:
        listing = _get_listing(conn, args.id)
        if not listing.url:
            sys.exit("This listing has no URL to apply to.")
        # Safety rail: never auto-apply to one company more than attempts.CAP times —
        # repeat runs are the fastest way to get flagged somewhere you may want to work.
        if not attempts.allowed(conn, listing.company):
            sys.exit(f"Safety cap: already auto-applied to {listing.company!r} "
                     f"{attempts.CAP} times. Apply by hand this time to avoid flagging a "
                     f"company you may want to work for.\n(Reset with: python main.py "
                     f"attempts reset \"{listing.company}\")")
        attempts.record(conn, listing.company)
    finally:
        conn.close()
    print(f"Applying to: {listing.title} @ {listing.company}")
    print(f"  {listing.url}")
    print("NOTE: run this on your Windows host (needs a real browser + Playwright).")
    # keep_open: browser stays up until you close it (used when launched from the
    # dashboard, which has no terminal to press Enter at).
    auto_close = getattr(args, "auto_close", False)
    if applier.is_workday(listing.url):
        from jobagent.workday import filler
        filler.fill_application(listing.url, wait_for_close=args.keep_open,
                                auto_close=auto_close)
    else:
        applier.fill_application(listing.url, wait_for_close=args.keep_open)
    if args.keep_open or auto_close:
        return  # auto_close/keep_open are non-interactive — no submit prompt
    ans = input("Mark this application as 'applied'? [y/N] ").strip().lower()
    if ans == "y":
        conn = database.connect()
        try:
            database.set_status(conn, args.id, "applied")
            conn.commit()
        finally:
            conn.close()
        print("Marked applied.")


def cmd_queue(args):
    """Batch-apply queue: `add` jobs, `list` state, or `run` them unattended.

    `run` drives each Workday application through the wizard and stops at Review —
    it NEVER submits. Generic single-page ATSs are left for the dashboard (an
    unattended run must not block on their submit prompt), so they're flagged
    needs_human rather than hanging the batch. Must run on your host (needs the
    browser + logins).
    """
    from jobagent import applyqueue, applier, attempts

    conn = database.connect()
    try:
        if args.action == "add":
            if not args.ids:
                sys.exit("usage: python main.py queue add <id> [<id> ...]")
            for lid in args.ids:
                if not conn.execute("SELECT 1 FROM listings WHERE id=?", (lid,)).fetchone():
                    print(f"  skip {lid}: no such listing")
                    continue
                applyqueue.enqueue(conn, lid)
                print(f"  queued {lid}")
        elif args.action == "list":
            rows = applyqueue.pending(conn)
            if not rows:
                print("queue empty.")
            for r in rows:
                print(f"  {r['state']:<12} {r['listing_id']}  {r['detail'] or ''}")
        elif args.action == "run":
            def apply_one(lid: str):
                row = conn.execute(
                    "SELECT url, company FROM listings WHERE id=?", (lid,)).fetchone()
                url = row["url"] if row else ""
                company = row["company"] if row else ""
                if not url:
                    raise RuntimeError("listing has no URL")
                if not attempts.allowed(conn, company):
                    # Company cap hit — skip it, don't hammer an employer you may want.
                    return ("needs_human",
                            f"safety cap: {attempts.CAP} auto-applies to {company} already — "
                            f"apply by hand")
                if not applier.is_workday(url):
                    return ("needs_human", "generic ATS — finish from the dashboard")
                print(f"\n=== applying {lid} -> {url} ===")
                from jobagent.workday import filler
                attempts.record(conn, company)
                filler.fill_application(url, auto_close=True, listing_id=lid)  # stops at Review, never submits
                return "filled"

            print("NOTE: run this on your Windows host (needs a real browser + logins).")
            freed = applyqueue.reset_stuck(conn)  # clear rows a prior crashed run left 'running'
            if freed:
                print(f"(reset {freed} job(s) left stuck 'running' by an earlier run)")
            done = applyqueue.run(conn, apply_one)
            print(f"\nQueue done: {done} application(s) processed and left at Review. "
                  f"Open the dashboard (Queue tab) to review each and click Submit.")
    finally:
        conn.close()


def cmd_attempts(args):
    """Show or reset the per-company auto-apply safety cap."""
    from jobagent import attempts

    conn = database.connect()
    try:
        if args.action == "reset":
            attempts.reset(conn, args.company)
            print(f"Reset attempt count for {args.company!r}." if args.company
                  else "Reset all attempt counts.")
            return
        rows = attempts.all_counts(conn)
        if not rows:
            print("No auto-apply attempts recorded yet.")
            return
        print(f"Per-company auto-apply attempts (cap = {attempts.CAP}):")
        for r in rows:
            flag = "  <- AT CAP" if r["count"] >= attempts.CAP else ""
            print(f"  {r['count']:>2}x  {r['company']}  (last {r['last']}){flag}")
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Personal job-search agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    su = sub.add_parser("setup", help="parse resume + targets -> profile.json")
    su.add_argument("--force", action="store_true", help="overwrite existing profile.json")
    su.set_defaults(func=cmd_setup)
    sub.add_parser("scan", help="pull + score jobs from configured sources").set_defaults(func=cmd_scan)

    l = sub.add_parser("list", help="show ranked matches")
    l.add_argument("--limit", type=int, default=30)
    l.add_argument("--all", action="store_true", help="don't filter by location")
    l.set_defaults(func=cmd_list)

    t = sub.add_parser("tailor", help="print tailoring brief for a job")
    t.add_argument("id"); t.set_defaults(func=cmd_tailor)

    r = sub.add_parser("render", help="validate Claude's draft + write docs")
    r.add_argument("id"); r.add_argument("--force", action="store_true")
    r.add_argument("--yes", action="store_true"); r.set_defaults(func=cmd_render)

    s = sub.add_parser("status", help="update application status")
    s.add_argument("id"); s.add_argument("new_status")
    s.add_argument("--follow-up", default=None); s.add_argument("--notes", default=None)
    s.set_defaults(func=cmd_status)

    sm = sub.add_parser("summarize", help="cache a short AI summary per job (needs local Ollama)")
    sm.add_argument("--limit", type=int, default=200)
    sm.add_argument("--all", action="store_true", help="include jobs outside your locations")
    sm.add_argument("--model", default="gemma4:e4b")
    sm.add_argument("--base", default="http://localhost:11434")
    sm.set_defaults(func=cmd_summarize)

    sub.add_parser("export", help="write Excel tracker").set_defaults(func=cmd_export)

    sub.add_parser("doctor", help="check the environment is ready (run first on a new host)"
                   ).set_defaults(func=cmd_doctor)

    sub.add_parser("workday-init", help="create the Workday answer bank template"
                   ).set_defaults(func=cmd_workday_init)

    ap = sub.add_parser("apply", help="open + autofill an application (host only)")
    ap.add_argument("id")
    ap.add_argument("--keep-open", action="store_true",
                    help="leave the browser open until you close it (used by the dashboard)")
    ap.add_argument("--trace", action="store_true",
                    help="record a Playwright trace + failure snapshots to output/traces/")
    ap.add_argument("--auto-close", action="store_true",
                    help="autonomous debug loop: after autofill, save a final screenshot + "
                         "log validation errors, then close the browser and exit (no prompts)")
    ap.set_defaults(func=cmd_apply)

    qp = sub.add_parser("queue",
                        help="batch-apply: queue jobs, then grind them unattended (each stops at Review)")
    qp.add_argument("action", choices=["add", "list", "run"])
    qp.add_argument("ids", nargs="*", help="listing ids (for 'add')")
    qp.set_defaults(func=cmd_queue)

    at = sub.add_parser("attempts",
                        help="show or reset the per-company auto-apply safety cap")
    at.add_argument("action", nargs="?", choices=["show", "reset"], default="show")
    at.add_argument("company", nargs="?", help="company to reset (default: all)")
    at.set_defaults(func=cmd_attempts)
    return p


def main(argv=None):
    _force_utf8_io()
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
