"""Shared helpers for sources."""
from __future__ import annotations

import datetime as _dt
import html as _html
import re

import requests

from ..models import Listing

USER_AGENT = "job-search-agent/0.1 (personal use)"
TIMEOUT = 20

# One shared session so every board fetch reuses the TCP+TLS connection
# instead of paying a fresh handshake per request.
_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = USER_AGENT


def now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def strip_html(raw: str) -> str:
    """Turn (possibly entity-encoded) HTML into readable plain text.

    Greenhouse returns its `content` entity-encoded (``&lt;div&gt;`` rather than
    ``<div>``), so we decode entities first, then strip real tags. A second
    unescape pass handles content that was double-encoded.
    """
    text = _html.unescape(raw or "")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def get_json(url: str) -> dict | list:
    resp = _SESSION.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def make_listing(**kw) -> Listing:
    kw.setdefault("fetched_at", now_iso())
    return Listing(**kw)
