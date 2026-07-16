"""failpacket writes a per-attempt evidence folder + INDEX line, and never
raises out of capture. No browser needed — this is the whole point: the packet
must be reconstructable offline."""
from __future__ import annotations

import pytest

from jobagent import failpacket


@pytest.fixture(autouse=True)
def _tmp_traces(tmp_path, monkeypatch):
    """Point failpacket at a throwaway traces dir so tests don't touch output/."""
    traces = tmp_path / "traces"
    monkeypatch.setattr(failpacket, "TRACES", traces)
    monkeypatch.setattr(failpacket, "INDEX", traces / "INDEX.md")
    monkeypatch.setattr(failpacket, "_current", None)
    return traces


def test_stuck_attempt_writes_notes_and_index(_tmp_traces):
    d = failpacket.start(listing_id="job-42", url="https://acme.wd5.myworkdayjobs.com/x")
    assert d is not None and d.is_dir()
    assert failpacket.attempt_dir() == d
    # a screenshot the driver would have taken
    (d / "review.png").write_bytes(b"png")

    failpacket.finish("needs_human",
                      errors=["Reason for leaving is required", "Field X invalid"],
                      step="reached Review")

    notes = (d / "NOTES.md").read_text()
    assert "NEEDS_HUMAN" in notes
    assert "Reason for leaving is required" in notes   # the actionable signal
    assert "review.png" in notes                       # evidence is listed
    index = _tmp_traces.joinpath("INDEX.md").read_text()
    assert "needs_human" in index and d.name in index
    assert failpacket.attempt_dir() is None            # attempt closed


def test_debug_tail_is_only_this_attempt(_tmp_traces, tmp_path):
    log = tmp_path / "apply-debug.log"
    log.write_text("OLD line from a previous application\n")
    d = failpacket.start(listing_id="j1", url="http://x", debug_log=log)
    log.write_text(log.read_text() + "NEW opened page\nNEW clicked next\n")

    failpacket.finish("error", errors=["boom"], step="exception")

    notes = (d / "NOTES.md").read_text()
    assert "NEW clicked next" in notes
    assert "OLD line from a previous application" not in notes


def test_clean_success_is_index_only(_tmp_traces):
    d = failpacket.start(listing_id="j2", url="http://x")
    failpacket.finish("filled")
    assert not (d / "NOTES.md").exists()               # no noise for a clean run
    assert "filled" in _tmp_traces.joinpath("INDEX.md").read_text()


def test_parked_questions_are_tagged_with_their_screenshot(_tmp_traces):
    d = failpacket.start(listing_id="j5", url="http://x")
    failpacket.note_shot("page-2-before")              # bot screenshots the page...
    failpacket.note_question("Do you hold a Series 7 license?")  # ...then can't answer it
    # a run that only PARKED questions (no errors) still isn't "clean" — we must review it
    failpacket.finish("filled", errors=[])

    notes = (d / "NOTES.md").read_text()
    assert "Do you hold a Series 7 license?" in notes
    assert "page-2-before.png" in notes                # question is tied to its picture
    assert "1 question(s) to answer" in _tmp_traces.joinpath("INDEX.md").read_text()


def test_note_question_when_idle_is_safe(_tmp_traces):
    failpacket.note_shot("x")                           # no attempt open
    failpacket.note_question("orphan")                  # must not raise


def test_finish_is_idempotent_and_safe(_tmp_traces):
    failpacket.start(listing_id="j3", url="http://x")
    failpacket.finish("error", errors=["e"])
    failpacket.finish("error", errors=["e"])           # second call must no-op
    lines = [ln for ln in _tmp_traces.joinpath("INDEX.md").read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    failpacket.finish("error")                          # finishing when idle: no crash


def test_capture_never_raises(_tmp_traces, monkeypatch):
    # Even if the folder can't be made, start() returns None and finish() is quiet.
    monkeypatch.setattr(failpacket.Path, "mkdir",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert failpacket.start(listing_id="j4") is None
    failpacket.finish("error", errors=["x"])            # must not raise
