"""The main dashboard shows how many parked questions are waiting on the human,
so a park during an overnight queue run can't go unnoticed."""
from jobagent import config, database, qbank


def _seed(db_path):
    database.init_db(db_path)


def test_badge_shows_pending_count(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    # isolate from real grounded facts — count only truly-parked questions
    monkeypatch.setattr("jobagent.workday.answer_bank.build_answers", lambda: {})
    _seed(config.DB_PATH)
    qbank.record_unknown("Do you hold a Series 7 license?")
    qbank.record_unknown("What are your preferred pronouns?")

    import dashboard
    html = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert "2 need you" in html


def test_grounded_questions_are_not_counted_as_needing_you(tmp_path, monkeypatch):
    """A parked question the guardrail can now answer from the résumé/My Info drops out of
    the 'need you' count (weeded non-destructively — the entry stays, just isn't surfaced)."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    monkeypatch.setattr("jobagent.workday.answer_bank.build_answers",
                        lambda: {"work_authorized": True})
    _seed(config.DB_PATH)
    qbank.record_unknown("Are you legally authorized to work in the US?")  # grounded -> Yes
    qbank.record_unknown("What are your preferred pronouns?")              # still parks

    import dashboard
    html = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert "1 need you" in html


def test_no_badge_when_nothing_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    _seed(config.DB_PATH)
    qbank.save({"Answered question?": "Yes"})  # answered ones don't count

    import dashboard
    html = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert "need you" not in html
