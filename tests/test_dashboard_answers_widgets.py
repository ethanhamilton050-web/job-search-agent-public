"""The /answers page renders the right widget per question kind (2026-07-13):
a below/average/above DROPDOWN for salary, an 'auto-answered' note (no input) for the
residence question, and a plain text box for everything else."""
from jobagent import qbank

SALARY_Q = "Please provide your salary expectations for this position."
RESIDE_Q = "Do you currently reside near one of the locations required for this job?"
PLAIN_Q = "Are you currently employed by PNC Bank?"


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({SALARY_Q: "", RESIDE_Q: "", PLAIN_Q: "No"})


def test_salary_renders_a_strategy_dropdown(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    import dashboard
    page = dashboard.app.test_client().get("/answers").data.decode()
    assert '<select name="a"' in page
    for opt in ("below", "average", "above"):
        assert f'value="{opt}"' in page


def test_residence_is_auto_answered_no_input(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    import dashboard
    page = dashboard.app.test_client().get("/answers").data.decode()
    assert "auto-answered" in page
    # residence must NOT be flagged as needing a manual answer
    reside_block = page.split(RESIDE_Q, 1)[1].split("</div>", 1)[0]
    assert "needs answer" not in reside_block


def test_plain_question_still_text_box(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    import dashboard
    page = dashboard.app.test_client().get("/answers").data.decode()
    assert 'type="text" name="a"' in page   # the plain question keeps a free-text input


def test_saving_a_dropdown_choice_persists(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    import dashboard
    c = dashboard.app.test_client()
    c.post("/answers", data={"q": SALARY_Q, "a": "average"})
    assert qbank.answer(SALARY_Q) == "average"


def test_typed_salary_number_keeps_a_text_box_not_the_dropdown(tmp_path, monkeypatch):
    """Adversarial near-miss (2026-07-15): a salary question answered with a LITERAL
    number, not a strategy word. The dropdown can't display it — it would render
    '— choose —' and the next Save would submit '' over the number, silently wiping
    the human's answer. It must render as a text box carrying the number."""
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({SALARY_Q: "85000"})
    import dashboard
    page = dashboard.app.test_client().get("/answers").data.decode()
    assert '<select name="a"' not in page          # no dropdown for a literal answer
    assert 'value="85000"' in page                 # the number rides in the form
    assert 'type="text" name="a"' in page          # editable, and Save round-trips it


def test_typed_salary_number_survives_a_save_roundtrip(tmp_path, monkeypatch):
    """What the browser would actually submit from the fixed page must keep the number."""
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({SALARY_Q: "85000"})
    import dashboard
    dashboard.app.test_client().post("/answers", data={"q": SALARY_Q, "a": "85000"})
    assert qbank.answer(SALARY_Q) == "85000"


def test_save_confirms_with_a_saved_chip(tmp_path, monkeypatch):
    """Saving /answers used to reload the page with zero feedback (2026-07-15) —
    /profile already confirms with a chip; answers now matches."""
    _seed(tmp_path, monkeypatch)
    import dashboard
    c = dashboard.app.test_client()
    resp = c.post("/answers", data={"q": PLAIN_Q, "a": "No"})
    assert "saved=1" in resp.headers["Location"]
    page = c.get(resp.headers["Location"]).data.decode()
    assert 'class="saved"' in page


def test_strategy_word_in_any_case_still_selects_its_dropdown_option(tmp_path, monkeypatch):
    """'Average' (capitalized) is still the strategy, not a literal — the dropdown must
    render with that option selected, or the browser shows '— choose —' and Save wipes it."""
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({SALARY_Q: "Average"})
    import dashboard
    page = dashboard.app.test_client().get("/answers").data.decode()
    assert '<select name="a"' in page
    assert 'value="average" selected' in page
