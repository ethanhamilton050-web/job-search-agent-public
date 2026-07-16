"""`scan` now best-effort triggers AI summaries for the newest jobs right after
scoring/saving them -- capped low and silently skipped if Ollama isn't running,
so it can never turn a routine scan into a long wait or a crash.
"""
import json
from types import SimpleNamespace

from jobagent import config, database, summarize


def _write_profile(path):
    path.write_text(json.dumps({"name": "Test User", "skills": [], "experience": []}),
                    encoding="utf-8")


def test_scan_calls_summarize_and_survives_ollama_being_down(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "jobs.db")
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path)
    monkeypatch.setattr(config, "PROFILE_PATH", profile_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")  # -> defaults (no sources)

    import main
    monkeypatch.setattr(main, "_scan_sources", lambda cfg: [])  # no real network
    # Deterministic regardless of whether Ollama actually happens to be running
    # on the machine running this test.
    monkeypatch.setattr(summarize, "ollama_reachable", lambda *a, **k: False)

    calls = []
    real_cmd_summarize = main.cmd_summarize
    def spy(args):
        calls.append(args)
        return real_cmd_summarize(args)
    monkeypatch.setattr(main, "cmd_summarize", spy)

    main.cmd_scan(SimpleNamespace())

    assert len(calls) == 1
    assert calls[0].limit == 20  # capped lower than the manual command's own default
    assert "Ollama not reachable" in capsys.readouterr().out
