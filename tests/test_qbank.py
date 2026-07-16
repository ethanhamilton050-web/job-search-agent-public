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


def test_save_merges_instead_of_wiping_concurrent_entries(tmp_path, monkeypatch):
    """Regression (found live, 2026-07-09, by an overnight adversarial audit): a
    save() built from a stale page snapshot must not erase a question a
    concurrently-running apply worker parked after that snapshot was taken."""
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.record_unknown("Are you 18 or older?")  # e.g. the /answers page's snapshot

    # a worker parks a brand-new question AFTER the snapshot was taken
    qbank.record_unknown("Do you require sponsorship?")

    # the human's Save only knew about the FIRST question (stale snapshot)
    qbank.save({"Are you 18 or older?": "Yes"})

    assert qbank.answer("Are you 18 or older?") == "Yes"
    assert qbank.answer("Do you require sponsorship?") == ""  # still parked, not erased
    assert "Do you require sponsorship?" in qbank.pending()


def test_save_loose_match_updates_existing_entry_not_a_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.record_unknown("Are you currently employed by PNC?")
    qbank.save({"are you currently employed by pnc": "Yes"})  # differently cased/punctuated
    assert len(qbank.load()) == 1  # updated the existing key, not a new shadow entry
    assert qbank.answer("Are you currently employed by PNC?") == "Yes"


def test_load_survives_malformed_json(tmp_path, monkeypatch):
    store = tmp_path / "s.json"
    store.write_text('{"Are you 18?": "Yes", ', encoding="utf-8")  # truncated mid-write
    monkeypatch.setattr(qbank, "STORE", store)
    assert qbank.load() == {}
    assert qbank.answer("Are you 18?") == ""
    assert qbank.pending() == []


def test_load_survives_wrong_top_level_shape(tmp_path, monkeypatch):
    store = tmp_path / "s.json"
    store.write_text("[]", encoding="utf-8")  # valid JSON, wrong shape (not an object)
    monkeypatch.setattr(qbank, "STORE", store)
    assert qbank.load() == {}
    assert qbank.pending() == []


def test_answer_and_pending_tolerate_a_non_string_stored_value(tmp_path, monkeypatch):
    store = tmp_path / "s.json"
    store.write_text('{"How many years?": 5}', encoding="utf-8")  # non-string value
    monkeypatch.setattr(qbank, "STORE", store)
    assert qbank.answer("How many years?") == "5"
    assert qbank.pending() == []
