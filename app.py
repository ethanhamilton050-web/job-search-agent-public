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
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


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


def main() -> None:
    import webview  # pip install pywebview

    threading.Thread(target=_serve, daemon=True).start()
    _wait_up()
    webview.create_window("Job Search Agent", URL, width=1100, height=820)
    webview.start()


if __name__ == "__main__":
    main()
