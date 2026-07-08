"""Screening-answer memory: loose matching, parking unknowns, pending list."""
from jobagent import qbank


def test_record_match_and_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")

    # unknown question is parked blank -> shows as pending, never auto-answered
    assert qbank.answer("Are you currently employed by PNC?") == ""
    qbank.record_unknown("Are you currently employed by PNC?")
    assert qbank.pending() == ["Are you currently employed by PNC?"]

    # re-recording the same question (different case/space/punct) is a no-op
    qbank.record_unknown("are you currently employed by pnc")
    assert len(qbank.load()) == 1

    # once answered, it's remembered and matches loosely; no longer pending
    qbank.save({"Are you currently employed by PNC?": "No"})
    assert qbank.answer("are you currently  employed by PNC") == "No"
    assert qbank.pending() == []
