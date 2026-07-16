# Packaging the desktop app

Goal (PRODUCT_PLAN.md, Decision 2): ship the Flask dashboard as a native desktop
app so a non-technical user double-clicks one icon — no Python, no terminal.

**Status (2026-07-08): steps 1 & 2 built and verified working with REAL data,
including two gotchas that only showed up from actually testing thoroughly
(not just "does it launch").** `pip install pywebview pyinstaller`, then
`python app.py` and the frozen `dist/JobSearchAgent/JobSearchAgent.exe` were
both run on the real host. Proved, not assumed: the bundled Chromium actually
navigates and renders a real page (not just launches), AND the app correctly
finds your real `profile.json`/`config.json`/`jobs.db` and shows your real
listing count. **Still unverified:** whether the frozen app can drive a real
Workday application (opening a real browser via Playwright to an actual
employer site hasn't been tried) — that needs Ethan, a real login, and a
live page, not something provable from a smoke test.

**Gotcha #2 — the frozen app couldn't find your real data at all.** A frozen
build's own `__file__` resolves inside the bundle's internal folder
(`dist/JobSearchAgent/_internal/...`), not the real project folder — so
`jobagent/config.py`'s `ROOT` (and therefore `profile.json`, `config.json`,
`data/jobs.db`, `input/`, `output/`) was silently pointing at a location
inside `_internal/` that never has your real files. Caught this by making the
Chromium verification actually navigate to the dashboard and noticing it said
"0 listings" instead of your real count — an empty-but-working dashboard
looks identical to a broken one unless you check the numbers. Fixed: `ROOT`
now uses the .exe's own folder when frozen (`jobagent/config.py:_detect_root`,
`tests/test_config.py`), the one stable, user-visible location a packaged app
has. Confirmed by copying real `profile.json`/`config.json`/`data/` next to
the frozen exe and watching it show your real filtered listing count.

**Operational consequence: PyInstaller wipes `dist/JobSearchAgent/` on every
freeze.** After each rebuild, copy your real data back in before running it:

```
copy config.json profile.json dist\JobSearchAgent\
xcopy /E /I data dist\JobSearchAgent\data
```

(`.env` and `workday_answers.json` too, once you're testing an actual apply —
not needed just to view the dashboard.) A future improvement worth considering
before this goes to other users: move real data to a stable per-user location
like `%APPDATA%\JobSearchAgent\` that survives a rebuild/reinstall, instead of
"next to the exe." Not done here — out of scope for proving the beta works on
this one machine.

**Gotcha #3 — the Apply and Run-queue buttons would have silently done
nothing.** Both shell out via `subprocess.Popen([sys.executable, str(MAIN_PY),
...])` — but in a frozen build, `sys.executable` IS `JobSearchAgent.exe`
itself, not a real Python interpreter, and there's no standalone `main.py`
file sitting on disk to point at either (same root cause as Gotcha #2 — a
frozen `__file__` doesn't resolve to a real on-disk path). Clicking Apply
would have launched another copy of the GUI (ignoring the extra args) instead
of actually running the autofiller — the core feature of the whole product,
silently broken. Fixed: `app.py`'s own entry point now checks
`len(sys.argv) > 1` and dispatches straight to `main.py`'s CLI logic instead
of opening the GUI when invoked with CLI-style args; `dashboard.py`'s new
`_cli_command()` helper builds `[sys.executable, "apply", "<id>",
"--keep-open"]` (no `main.py` path) when frozen, the old
`[sys.executable, main.py, ...]` form otherwise. **Verified for real**: ran
`JobSearchAgent.exe attempts` (a safe, read-only command) and it correctly
printed the real per-company attempt-cap data instead of opening a window —
proves the dispatch mechanism genuinely works, without needing to trigger a
live browser to check it. `tests/test_dashboard_cli_command.py`.

## 1. Native window (works now)

```
pip install pywebview
python app.py
```

This starts Flask on 127.0.0.1:5000 in a background thread and opens it in a
borderless native window.

## 2. Freeze to a single .exe (to be done on the host)

```
pip install pyinstaller
pyinstaller --noconfirm --windowed --name JobSearchAgent ^
  --collect-all playwright ^
  --add-data "%LOCALAPPDATA%\ms-playwright;ms-playwright" ^
  app.py
```

Output: `dist/JobSearchAgent/` (~835MB) + `build/` (~25MB), both gitignored.
Double-click `dist/JobSearchAgent/JobSearchAgent.exe` to try it yourself.
Bigger than a bare freeze (~148MB) on purpose — see below.

**The one fiddly bit — bundling Playwright's Chromium — DONE (2026-07-08),
fully offline.** PyInstaller doesn't bundle the browser binaries themselves,
and worse: a frozen build resolves its expected Chromium path to an empty,
bundle-relative `.local-browsers` folder instead of the real shared cache at
`%LOCALAPPDATA%\ms-playwright` — confirmed by actually running an unbundled
frozen exe and watching it try to re-download Chromium every launch even
though it was already installed on the machine.

Chose the fully-bundled option over "download on first launch" — priority was
"just make it work, don't care about size." The `--add-data` flag above copies
your ENTIRE local `%LOCALAPPDATA%\ms-playwright` cache (~685MB: Chromium +
headless shell + ffmpeg + winldd) straight into the frozen app, so it works
offline from the very first launch, no network dependency at all.

`app.py`'s `_ensure_chromium()` (runs once at startup, before the window
opens) checks in priority order: (1) the bundled copy inside this frozen build
(`sys._MEIPASS/ms-playwright`) — this is the one we ship with and the one that
actually gets used; (2) the real shared system cache (dev machine, or an
older/unbundled build); (3) if neither has a working browser, download it on
the spot as a last-resort safety net (in-process, via
`playwright.__main__.main()` with a swapped `sys.argv` — no subprocess/separate
Python needed, works inside a frozen exe since `playwright` itself is
bundled). The check itself launches-and-closes a real Chromium instance, not
just a file-exists check — a missing DLL or partially-copied bundle wouldn't
show up in a path check alone.

Verified for real, twice: re-froze after the fix and confirmed launch is
instant with no fallback-download message; then rebuilt again with the
stronger launch-and-close check and confirmed the BUNDLED Chromium actually
starts and closes cleanly from inside the frozen exe (not just that a file
sits at the right path).

Ceiling: this only proves Chromium *launches* on THIS host from inside the
frozen build. It doesn't yet prove the frozen app can drive a real Workday
application end-to-end — that needs Ethan, a real login, and a live page, not
something provable from a smoke test.

## 3. Code signing (needs you)

Unsigned Windows apps trigger SmartScreen ("unknown publisher"). For a sellable
product, buy a code-signing certificate (OV ~ under $100/yr; EV avoids the
SmartScreen warning entirely but costs more and needs a hardware token) and sign
`JobSearchAgent.exe` with `signtool`. This is tied to your identity + wallet, so
it's yours to do.

## Skipped for v1 (add when it hurts)

- Auto-update (PRODUCT_PLAN.md already defers this).
- macOS build (Windows-only to start).
- Installer (.msi) — a zipped folder or single .exe is fine for a beta.
