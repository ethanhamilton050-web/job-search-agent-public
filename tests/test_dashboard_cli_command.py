"""A frozen build's sys.executable IS the app itself, not a python.exe, and
there's no separate on-disk main.py to point at either -- the Apply and
Run-queue buttons' subprocess launch must build a different argv when frozen
(see app.py's own argv dispatch) or they'd silently do nothing useful.
"""
import sys

import dashboard


def test_cli_command_uses_main_py_path_when_not_frozen():
    assert not getattr(sys, "frozen", False)
    cmd = dashboard._cli_command("apply", "job-1", "--keep-open")
    assert cmd == [sys.executable, str(dashboard.MAIN_PY), "apply", "job-1", "--keep-open"]


def test_cli_command_skips_main_py_path_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\fake\dist\JobSearchAgent\JobSearchAgent.exe")
    cmd = dashboard._cli_command("queue", "run")
    assert cmd == [r"C:\fake\dist\JobSearchAgent\JobSearchAgent.exe", "queue", "run"]
