"""Configuration loading: non-secret run params from config.json, secrets from .env."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
TAILORED_DIR = OUTPUT_DIR / "tailored"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "jobs.db"
PROFILE_PATH = ROOT / "profile.json"
CONFIG_PATH = ROOT / "config.json"

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
            if isinstance(values, dict) and section in cfg:
                cfg[section].update(values)
            else:
                cfg[section] = values
    return cfg


def ensure_dirs() -> None:
    for d in (INPUT_DIR, OUTPUT_DIR, TAILORED_DIR, DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)
