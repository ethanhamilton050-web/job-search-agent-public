"""load_config's merge-over-defaults must not silently swallow a typo.

Regression found live, 2026-07-09, by an overnight adversarial audit: a
mistyped top-level section name (e.g. "scoreing" instead of "scoring") used
to be silently added as an inert extra key, leaving the REAL section at 100%
of its defaults with no error telling the user why their edit had no effect.
"""
import json

from jobagent import config


def test_typo_d_section_warns_and_is_ignored(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"scoreing": {"min_score_to_show": 90}}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)

    cfg = config.load_config()

    assert cfg["scoring"]["min_score_to_show"] == config.DEFAULT_CONFIG["scoring"]["min_score_to_show"]
    assert "scoreing" not in cfg
    assert "scoreing" in capsys.readouterr().out


def test_recognized_section_still_merges_normally(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"scoring": {"min_score_to_show": 90}}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)

    cfg = config.load_config()

    assert cfg["scoring"]["min_score_to_show"] == 90
    # untouched keys in the same section keep their defaults
    assert cfg["scoring"]["weight_skill_overlap"] == config.DEFAULT_CONFIG["scoring"]["weight_skill_overlap"]


def test_leading_underscore_comment_keys_are_silently_skipped(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"_comment": "just a note"}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)

    cfg = config.load_config()

    assert "_comment" not in cfg
    assert capsys.readouterr().out == ""  # a real comment convention, not a typo -- no warning
