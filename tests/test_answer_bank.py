"""Tests for the Workday answer bank (data layer; no browser needed)."""
import json

from jobagent.workday import answer_bank
from jobagent import config


def _write_profile(tmp_path, monkeypatch):
    prof = {
        "name": "Jane Doe",
        "contact": {"email": "e@x.com", "phone": "555-555-0100"},
        "skills": ["Excel", "Audit"],
        "experience": [
            {"company": "Acme – NJ", "title": "Analyst", "dates": "2022 - 2024",
             "bullets": ["Did a thing", "Did another"]}
        ],
        "education": ["B.S. Finance"],
        "raw_text": "x",
        "targets": {},
    }
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(prof), encoding="utf-8")
    monkeypatch.setattr(config, "PROFILE_PATH", p)
    monkeypatch.setattr(answer_bank, "ANSWERS_PATH", tmp_path / "workday_answers.json")
    monkeypatch.setattr(config, "INPUT_DIR", tmp_path)  # no resume file -> empty


def test_build_answers_from_profile(tmp_path, monkeypatch):
    _write_profile(tmp_path, monkeypatch)
    ans = answer_bank.build_answers()
    assert ans["first_name"] == "Ethan"
    assert ans["last_name"] == "Hamilton"
    assert ans["email"] == "e@x.com"
    assert ans["experience"][0]["company"] == "Acme"   # location stripped
    assert ans["experience"][0]["title"] == "Analyst"
    assert ans["needs_sponsorship"] is False


def test_overlay_overrides(tmp_path, monkeypatch):
    _write_profile(tmp_path, monkeypatch)
    (tmp_path / "workday_answers.json").write_text(
        json.dumps({"how_did_you_hear": "Referral", "github_url": "gh.com/x"}),
        encoding="utf-8",
    )
    ans = answer_bank.build_answers()
    assert ans["how_did_you_hear"] == "Referral"
    assert ans["github_url"] == "gh.com/x"
