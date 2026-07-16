"""A frozen PyInstaller build's own __file__ resolves inside the bundle's
internal folder, not the real app folder -- confirmed by actually running a
frozen build and finding it silently unable to find profile.json. ROOT must
use the .exe's own directory when frozen, and the normal source-relative path
otherwise.
"""
import sys

from jobagent import config


def test_root_is_source_relative_when_not_frozen():
    assert not getattr(sys, "frozen", False)
    assert config._detect_root() == config.ROOT
    assert (config.ROOT / "jobagent").is_dir()


def test_root_is_exe_relative_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\fake\dist\JobSearchAgent\JobSearchAgent.exe")
    from pathlib import Path
    assert config._detect_root() == Path(r"C:\fake\dist\JobSearchAgent")
