"""Configuration loading: non-secret run params from config.json, secrets from .env."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


def _detect_root() -> Path:
    """Where profile.json/config.json/data/input/output actually live.

    A frozen PyInstaller build's `__file__` resolves to somewhere inside the
    bundle's own internal folder (`dist/<app>/_internal/...`), NOT the real,
    stable app folder a user's real data should live in -- confirmed by
    actually running a frozen build and finding it silently looking for
    profile.json inside `_internal/`, never finding it. Use the .exe's own
    directory instead when frozen; that's the one stable, user-visible folder
    a packaged app has.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _detect_root()
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
TAILORED_DIR = OUTPUT_DIR / "tailored"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "jobs.db"
PROFILE_PATH = ROOT / "profile.json"
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_CONFIG_PATH = ROOT / "config.example.json"

load_dotenv(ROOT / ".env")

DEFAULT_CONFIG = {
    "targets": {
        "titles": [],
        "locations": ["Remote"],
        "remote_ok": True,
        "work_authorization": "",
        "salary_floor": 0,
        "keywords": [],
    },
    "sources": {
        "greenhouse_boards": [],
        "lever_boards": [],
        "workday_sites": [],
    },
    "scoring": {
        "weight_skill_overlap": 0.5,
        "weight_title_match": 0.3,
        "weight_keyword_match": 0.2,
        "min_score_to_show": 40,
    },
}


def load_config() -> dict:
    """Load config.json merged over defaults. Falls back to defaults if missing."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for section, values in user.items():
            if section.startswith("_"):
                continue
            if section not in cfg:
                # A typo'd section name (e.g. "scoreing" instead of "scoring")
                # used to just get silently added as an inert extra key, with
                # the REAL section quietly keeping 100% of its defaults and no
                # error telling you why your edit had no effect. Found live,
                # 2026-07-09, by an overnight adversarial audit.
                print(f"  ! config.json: unrecognized section {section!r} -- "
                      f"typo? (known: {', '.join(sorted(cfg))}) -- ignored")
                continue
            if isinstance(values, dict):
                cfg[section].update(values)
            else:
                cfg[section] = values
    return cfg


def ensure_config(config_path: Path | None = None,
                  example_path: Path | None = None) -> None:
    """First run: seed a neutral starter config.json from config.example.json so a
    fresh install has a usable, documented file to edit instead of invisible empty
    defaults. Never overwrites an existing config.json, so it can't clobber a
    user's settings. (F12: keeps a customer's out-of-the-box run from being blank
    -- or, if someone's config is ever shipped, gives a clean neutral starting
    point.)"""
    config_path = config_path or CONFIG_PATH
    example_path = example_path or EXAMPLE_CONFIG_PATH
    if not config_path.exists() and example_path.exists():
        config_path.write_text(example_path.read_text(encoding="utf-8"),
                               encoding="utf-8")


def ensure_dirs() -> None:
    for d in (INPUT_DIR, OUTPUT_DIR, TAILORED_DIR, DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)
    ensure_config()
