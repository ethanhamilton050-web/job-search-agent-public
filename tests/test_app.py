"""app.py must stay import-safe (no eager pywebview/playwright import at module
scope) so the CLI/dashboard/test suite never need those host-only packages
installed. The actual Chromium-bootstrap behavior is verified manually against
a real Playwright install, same as everything else touching a real browser.
"""


def test_app_module_imports_without_pywebview_or_playwright():
    import app
    assert callable(app.main)
    assert callable(app._ensure_chromium)
    assert callable(app._serve)
    assert callable(app._wait_up)


def test_bundled_browsers_dir_is_none_when_not_frozen():
    import app
    assert app._bundled_browsers_dir() is None


def test_bundled_browsers_dir_points_at_meipass_when_frozen(monkeypatch):
    import sys
    from pathlib import Path
    import app
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\fake\dist\JobSearchAgent\_internal", raising=False)
    assert app._bundled_browsers_dir() == Path(r"C:\fake\dist\JobSearchAgent\_internal") / "ms-playwright"


def test_main_fails_fast_without_opening_a_window_when_server_never_comes_up(monkeypatch, capsys):
    """Regression (found live, 2026-07-09, by an overnight adversarial audit): the
    old code opened the webview window regardless of whether Flask ever came up
    (e.g. port 5000 already bound by another running instance), pointing it at a
    dead server with no visible error in a --windowed frozen build. Must fail
    loud and return WITHOUT ever importing webview -- proves the fix without
    needing pywebview installed."""
    import app
    monkeypatch.setattr(app, "_ensure_chromium", lambda: None)
    monkeypatch.setattr(app, "_serve", lambda: None)
    monkeypatch.setattr(app, "_wait_up", lambda timeout=10.0: False)
    app.main()
    assert "Could not reach" in capsys.readouterr().out
