"""The grounded-facts store + the My Info form parser.

Parsing is framework-agnostic (single/many callables) so it tests without Flask. The
store must round-trip, keep a backup, and NEVER crash an apply run on a corrupt file.
"""
from jobagent import facts


def _form(d):
    """Return (single, many) accessors over a dict whose list values are 'many' fields."""
    single = lambda k: ("" if isinstance(d.get(k), list) else d.get(k))
    many = lambda k: (d.get(k) if isinstance(d.get(k), list) else [])
    return single, many


def test_parse_profile_form_builds_grounded_facts():
    single, many = _form({
        "highest_education": "  MBA  ",
        "home_country": "United States of America",
        "employers": "Goldman Sachs\n  Morgan Stanley  \n\nDeloitte\n",
        "employment_history_complete": "on",
        "insider_who": ["self", "mother", ""],
        "insider_company": ["ACME", "XYZ Corp", ""],
        "insider_ticker": ["ACM", "", ""],
        "insider_role": ["Director", "Officer", ""],
        "insider_amount": ["12%", "3,000 sh", ""],
        "gov_who": ["", ""], "gov_role": ["", ""], "gov_entity": ["", ""],
        "related_party_complete": "on",
    })
    out = facts.parse_profile_form(single, many)
    assert out["highest_education"] == "MBA"                       # trimmed
    assert out["employers_all"] == ["Goldman Sachs", "Morgan Stanley", "Deloitte"]  # blanks dropped
    assert out["employment_history_complete"] is True
    assert out["insiders"] == [
        {"who": "self", "company": "ACME", "ticker": "ACM", "role": "Director", "amount": "12%"},
        {"who": "mother", "company": "XYZ Corp", "ticker": "", "role": "Officer", "amount": "3,000 sh"},
    ]                                                             # the empty 3rd row is dropped
    assert out["government_officials"] == []                       # all rows blank -> dropped
    assert out["related_party_complete"] is True


def test_more_rows_than_the_default_pad_all_persist():
    # The "add row" button lets Ethan list more insiders than the 3 blank rows the form
    # renders by default; the parser must keep every filled row it's handed (any N).
    n = 5
    single, many = _form({
        "insider_who": [f"rel{i}" for i in range(n)],
        "insider_company": [f"Co{i}" for i in range(n)],
        "insider_ticker": [""] * n,
        "insider_role": ["Director"] * n,
        "insider_amount": ["5%"] * n,
    })
    out = facts.parse_profile_form(single, many)
    assert len(out["insiders"]) == n
    assert out["insiders"][4]["company"] == "Co4"


def test_unchecked_boxes_are_false():
    single, many = _form({"employers": "Acme"})                    # no checkboxes submitted
    out = facts.parse_profile_form(single, many)
    assert out["employment_history_complete"] is False
    assert out["related_party_complete"] is False


def test_save_load_roundtrip_with_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(facts, "PATH", tmp_path / "g.json")
    monkeypatch.setattr(facts, "_BAK", tmp_path / "g.json.bak")
    assert facts.load() == {}                                     # missing file -> {}
    facts.save({"highest_education": "MBA", "insiders": []})
    assert facts.load()["highest_education"] == "MBA"
    facts.save({"highest_education": "PhD"})                       # overwrite
    assert facts.load()["highest_education"] == "PhD"
    assert (tmp_path / "g.json.bak").exists()                     # previous version preserved


def test_load_survives_a_corrupt_file(tmp_path, monkeypatch):
    p = tmp_path / "g.json"
    p.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(facts, "PATH", p)
    assert facts.load() == {}                                     # never crash an apply run
