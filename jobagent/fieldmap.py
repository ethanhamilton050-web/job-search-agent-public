"""Field-map cache: map a Workday form ONCE with a local LLM, then replay the
cached map deterministically forever.

The AI is OFF the live path. On a page we already know, `map_form` returns the
cached map with no network call. Only a NEW or drifted form triggers an LLM call,
and if the AI box is unreachable we return `(None, "none")` so the caller falls
back to the hand-tuned selectors and the app keeps working.

The cache is keyed by page STRUCTURE — automation-ids + field shape, with volatile
per-render ids and aria strings stripped — not by a specific tenant. Workday tenants
are ~90% the same product, so one solved page covers that page everywhere.

Only the blank field STRUCTURE is ever sent to the model; never a filled value,
answer, or credential (the caller passes `_dump_fields`-style descriptors, which
carry no user data).
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import config

CACHE_PATH = config.DATA_DIR / "fieldmaps.json"

# Drop the two volatile parts of a descriptor so the key is the page STRUCTURE:
#   id=yq173         -> Workday reassigns these every render
#   aria="State ..." -> carries the current value/selection, not structure
# The stable structure (tag[type], name=, aid=) is what remains.
_VOLATILE = re.compile(r'\s+id=\S+|\s+aria=(?:"[^"]*"|\S+)')


def _normalize(descriptor: str) -> str:
    return _VOLATILE.sub("", descriptor).strip()


# A pre-filled value can ride along as value="John" or value=John. Strip it wholesale:
# the model maps blank STRUCTURE, so the current value is never needed and must never leave.
_VALUE_ATTR = re.compile(r'\s+value=(?:"[^"]*"|\S+)')


def _redactable(v) -> str:
    """A single answer value as a plain string worth redacting, or "" to skip.

    We only redact real PII-length strings (name/email/phone/address). Booleans,
    numbers, and 1-2 char values are skipped — they cause false-positive matches
    (e.g. redacting "18" or "No" would gut structural tokens), and they aren't PII.
    """
    if isinstance(v, str):
        return v.strip() if len(v.strip()) >= 3 else ""
    return ""  # ponytail: bool/int/list/dict values aren't free-text PII; skip them


def _answer_values(answers: dict) -> list[str]:
    """Flatten the answer bank to the free-text strings worth redacting from a descriptor.

    Longest first so a longer value (full name) is redacted before its substrings.
    """
    vals: list[str] = []
    for v in answers.values():
        if isinstance(v, dict):
            vals.extend(_redactable(x) for x in v.values())
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    vals.extend(_redactable(x) for x in item.values())
                else:
                    vals.append(_redactable(item))
        else:
            vals.append(_redactable(v))
    return sorted({s for s in vals if s}, key=len, reverse=True)


def scrub(descriptors: list[str], answers: dict | None = None) -> list[str]:
    """Strip pre-filled VALUES from descriptors, keeping structure (tag[type], id, name, aid).

    Privacy is the plan's promise: only blank form STRUCTURE leaves the device. Always
    drops any value= attribute; if `answers` is given, also redacts any answer-bank value
    substring (name/email/phone) wherever it appears — including inside an aria= label,
    whose MEANING we keep but whose leaked PII we do not.
    """
    values = _answer_values(answers) if answers else []
    out = []
    for d in descriptors:
        d = _VALUE_ATTR.sub("", d)
        for v in values:
            if v in d:
                d = d.replace(v, "")
        out.append(d)
    return out


def signature(descriptors: list[str]) -> str:
    """Stable 16-char key for a form's structure (order-independent)."""
    norm = sorted(_normalize(d) for d in descriptors)
    return hashlib.sha1("\n".join(norm).encode("utf-8")).hexdigest()[:16]


def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}  # a corrupt/half-written cache must never crash a run
    return {}


def get(sig: str, cache_path: Path | None = None) -> list | None:
    return _load(cache_path or CACHE_PATH).get(sig)


def put(sig: str, mapping: list, cache_path: Path | None = None) -> None:
    path = cache_path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load(path)
    data[sig] = mapping
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def parse_map(text: str) -> list:
    """Pull the JSON array out of a model reply. Raises ValueError if there is none."""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        raise ValueError("no JSON array in model output")
    return json.loads(text[start:end + 1])


def call_llm(descriptors: list[str], model: str, base: str, timeout: int = 180,
             answers: dict | None = None) -> list:
    """Ask a local Ollama/Gemma box to map the fields. Stdlib only (no pip dep).

    Scrubs pre-filled values before building the prompt so no PII ever leaves the
    device — only blank field structure is sent (the plan's privacy promise).
    """
    descriptors = scrub(descriptors, answers)
    numbered = "\n".join(f"{i}: {d}" for i, d in enumerate(descriptors))
    prompt = (
        "You label form fields dumped from a Workday job-application page. Each line is\n"
        "one field: index, tag[type], and identifiers (id / name / aria / aid).\n\n"
        "For EACH field output its INTENT (what the field is for, e.g. first_name, email,\n"
        "phone, country, previous_worker, how_did_you_hear, ignore) and its fill STRATEGY\n"
        "(one of: text, select, radio, checkbox, date, file, ignore).\n"
        "A <button> or a field with aid=multiselectInputContainer that opens a dropdown is\n"
        "'select'; a plain text box is 'text'; page-chrome menu buttons are 'ignore'.\n\n"
        "FIELDS:\n" + numbered + "\n\n"
        'Reply with ONLY a JSON array: [{"i": 0, "intent": "...", "strategy": "..."}, ...]'
    )
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/api/chat", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = json.loads(resp.read())["message"]["content"]
    return parse_map(content)


def map_form(descriptors: list[str], *, model: str = "gemma2",
             base: str = "http://localhost:11434", cache_path: Path | None = None,
             allow_llm: bool = True, answers: dict | None = None) -> tuple[list | None, str]:
    """Return (mapping, source). source is 'cache' | 'llm' | 'none'.

    Cache-first. On a miss, calls the LLM only if allow_llm; any failure (box down,
    bad reply) returns (None, 'none') so the caller falls back to hand selectors —
    the app never breaks just because the mapping box is offline. `answers`, if
    given, is used only to redact PII from descriptors before they reach the model.
    """
    sig = signature(descriptors)
    cached = get(sig, cache_path)
    if cached is not None:
        return cached, "cache"
    if not allow_llm:
        return None, "none"
    try:
        mapping = call_llm(descriptors, model, base, answers=answers)
    except (urllib.error.URLError, ValueError, OSError, KeyError, TimeoutError):
        return None, "none"
    put(sig, mapping, cache_path)
    return mapping, "llm"
