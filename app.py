"""Desktop shell: the local dashboard in a native window (no browser chrome).

This is the v1 packaging target from PRODUCT_PLAN.md (Decision 2): the existing
Flask dashboard rendered via pywebview, later frozen with PyInstaller. Run it with:

    pip install pywebview
    python app.py

Build/freeze instructions (and the Chromium-bundling gotcha) are in PACKAGING.md.
Kept dependency-light and import-safe: pywebview is imported inside main(), so the
CLI, tests, and the plain `python dashboard.py` path never depend on it.
"""
from __future__ import annotations

import threading
import time
import urllib.request

HOST, PORT = "127.0.0.1", 5000
URL = f"http://{HOST}:{PORT}"


def _serve() -> None:
    from dashboard import app
    # threaded=True: see dashboard.py's own __main__ block for why -- the same
    # slow /coach/<lid> request would otherwise block the whole packaged app.
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


def _wait_up(timeout: float = 10.0) -> bool:
    """Flask starts on a background thread; don't open the window until it answers."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(URL, timeout=1)
            return True
        except OSError:
            time.sleep(0.2)
    return False


def _bundled_browsers_dir():
    """Where a frozen build's own bundled Chromium lives (see the --add-data
    step in PACKAGING.md), or None when running unfrozen (`python app.py`)."""
    import sys
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        from pathlib import Path
        return Path(sys._MEIPASS) / "ms-playwright"
    return None


def _ensure_chromium() -> None:
    """Make sure Playwright can find a real Chromium, in priority order:
    1. bundled INTO this frozen build (PACKAGING.md's --add-data step) -- works
       fully offline, first launch included; this is the one we ship with.
    2. the real shared cache another `playwright install` already filled
       (dev machine, or an older/unbundled build).
    3. download it now, on the spot -- the safety net so the app still works
       even from a build that skipped step 1, instead of just failing.

    # ponytail: option 3 blocks startup with a console message. Fine for
    # `python app.py` (console visible); the frozen --windowed build has no
    # console, so hitting this fallback there just pauses before the window
    # opens with no visible progress -- shouldn't come up since we bundle,
    # but add a splash/progress window if it ever does and bothers someone.
    """
    import os
    from pathlib import Path
    bundled = _bundled_browsers_dir()
    if bundled is not None and bundled.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
    else:
        os.environ.setdefault(
            "PLAYWRIGHT_BROWSERS_PATH",
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"))
    from playwright.sync_api import sync_playwright
    try:
        # A real launch-and-close, not just a file-exists check -- a missing DLL,
        # wrong architecture, or partially-copied bundle wouldn't show up in
        # executable_path alone, and "just make it work" means actually proving
        # the browser starts, not just that a file is sitting at the right path.
        with sync_playwright() as p:
            p.chromium.launch().close()
            return
    except Exception:
        pass  # couldn't launch -- fall through to the download-and-retry path below
    print("First launch: downloading the browser this app needs to apply for jobs "
          "(one-time, ~150-300MB, needs internet)...")
    import sys
    old_argv = sys.argv
    sys.argv = ["playwright", "install", "chromium"]
    try:
        from playwright.__main__ import main as playwright_install
        playwright_install()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv


def main() -> None:
    _ensure_chromium()
    threading.Thread(target=_serve, daemon=True).start()
    if not _wait_up():
        # Found live, 2026-07-09, by an overnight adversarial audit: the old
        # code opened the window regardless, pointed at a server that never
        # came up (e.g. port 5000 already bound by another running instance)
        # -- a --windowed frozen build has no console to show the real error,
        # so the user just saw a broken "can't connect" page inside the app
        # chrome with no clue why. Fail loud instead of opening a dead window.
        print(f"Could not reach {URL} -- is another copy of this app (or "
              f"`python dashboard.py`) already running? Close it and try again.")
        return
    import webview  # pip install pywebview
    webview.create_window("Job Search Agent", URL, width=1100, height=820)
    webview.start()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # A frozen build's sys.executable IS this exe -- there's no separate
        # python.exe + main.py to shell out to anymore. dashboard.py's Apply and
        # Run-queue buttons launch a subprocess with CLI-style args
        # (["apply", "<id>", "--keep-open"] / ["queue", "run"]) pointed straight
        # at sys.executable when frozen; dispatch those here to the exact same
        # CLI logic `python main.py ...` runs, instead of opening the GUI again.
        import main as cli_main
        cli_main.main()
    else:
        main()
