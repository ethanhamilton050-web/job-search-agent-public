# Packaging the desktop app

Goal (PRODUCT_PLAN.md, Decision 2): ship the Flask dashboard as a native desktop
app so a non-technical user double-clicks one icon — no Python, no terminal.

**Status: scaffolded, NOT yet built.** `app.py` runs the dashboard in a native
window today (`pip install pywebview && python app.py`). The freeze step below has
to be run and debugged **on the Windows host** — it can't be verified from the
agent sandbox. Treat the commands as a starting point, not a finished recipe.

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
  app.py
```

**The one fiddly bit — bundling Playwright's Chromium.** PyInstaller does not pick
up the browser binaries Playwright downloads into its cache. Two options:

- Ship them alongside and point Playwright at them at runtime by setting
  `PLAYWRIGHT_BROWSERS_PATH` to a folder you `--add-data` into the bundle, **or**
- Have the app run `playwright install chromium` on first launch (simpler to build,
  slower first run, needs network once).

Verify the frozen app can actually drive a real Workday page before trusting it —
the browser-launch path is exactly what breaks in a freeze.

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
