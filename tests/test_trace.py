"""Tracing is opt-in via JOBAGENT_TRACE. Verify it stays a no-op when off (so
normal runs are byte-for-byte unchanged) and that the flag actually toggles."""
from jobagent.workday import filler


def test_trace_off_by_default(monkeypatch):
    monkeypatch.delenv("JOBAGENT_TRACE", raising=False)
    assert filler._trace_on() is False
    # off => every helper bails before touching the (here invalid) context
    filler._start_trace(None)
    filler._stop_trace(None, "x")
    filler._dump_failure(None, "x")


def test_trace_flag_toggles(monkeypatch):
    monkeypatch.setenv("JOBAGENT_TRACE", "1")
    assert filler._trace_on() is True
