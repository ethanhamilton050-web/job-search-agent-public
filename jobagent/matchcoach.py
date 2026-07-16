"""Match Coach: suggest truthful resume-bullet rewrites toward a JD's missing
traits (AFTER_LAUNCH.md "Half B").

The AI only proposes; jobagent.tailor.validate is the receipt -- same
two-layer "receipt or nothing" spirit as guardrail.py, applied to generative
text instead of Q&A. A rewrite that adds/drops a number, %, $, or date is
dropped outright, never shown to the human. A new capitalized term (possible
invented tool/employer) is kept as a caution, not a hard block -- same
severity split tailor.py already uses; the human still eyeballs it before
using anything, same as tailor.py's existing diff-approval gate.

The AI call is injected (`rewrite_fn`) so tests never touch a network: pass a
plain function/lambda, same pattern as jobagent.screening's ai_answer_fn.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable

from . import tailor
from .models import ResumeProfile

RewriteFn = Callable[[str, str, list[str]], "tuple[str, str] | None"]


def _term_hits(term: str, text: str) -> int:
    """Word-boundary occurrence count — same boundary rule as scorer._term_in,
    so 'r'/'ai'/'c' can't count inside ordinary words."""
    return len(re.findall(
        rf"(?<![A-Za-z0-9+#]){re.escape(term)}(?![A-Za-z0-9+#])", text))


def coach_traits(profile: ResumeProfile, jd_text: str, keywords: list[str]) -> list[str]:
    """The coach's OWN target list: terms this JOB's text actually asks for that
    the resume never mentions — ordered by how often the JD repeats them (an
    employer that says 'Bloomberg' five times cares more about Bloomberg).

    Replaces reusing the score display's 'missing' string (ISSUES G), which was
    wrong for coaching twice over: capped at 5 alphabetically as a display
    artifact, and listing the OPPOSITE thing — profile skills the JD *doesn't*
    mention — while the coach prompt tells the AI "the posting wants these".
    """
    resume_text = " ".join(
        [profile.summary or ""]
        + [str(s) for s in (profile.skills or [])]
        + [b for exp in (profile.experience or []) for b in (exp.bullets or [])]
    ).lower()
    jd = (jd_text or "").lower()
    scored = []
    for k in dict.fromkeys(str(k).lower().strip() for k in keywords if str(k).strip()):
        if _term_hits(k, resume_text):
            continue  # the resume already says it — nothing to coach toward
        hits = _term_hits(k, jd)
        if hits:
            scored.append((-hits, k))
    return [k for _, k in sorted(scored)]


def _parse_json_object(text: str) -> dict:
    """Pull the JSON object out of a model reply, tolerating markdown fences or
    preamble text around it -- the same lesson jobagent.fieldmap.parse_map
    already learned for a JSON array. Found live, 2026-07-09, by an overnight
    adversarial audit: Ollama/gemma models commonly wrap a requested JSON reply
    in ```json fences (or add a stray sentence) even when told "ONLY JSON"; a
    raw json.loads() on the whole reply raised ValueError every time that
    happened, silently returning None for every bullet -- misleadingly
    surfaced on the dashboard as "the local AI box isn't running" when Ollama
    was actually fine, it was just a parsing gap.
    """
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def local_rewrite(bullet: str, jd_text: str, missing_traits: list[str], *,
                   model: str = "gemma4:e4b", base: str = "http://localhost:11434",
                   timeout: int = 60) -> tuple[str, str] | None:
    """Ask the local Ollama box to rephrase one bullet toward ONE missing trait.

    Stdlib only, same call shape as jobagent.summarize.ai_summary. Returns
    (rewritten_text, trait_it_addressed) or None if the model declines, is
    unreachable, or doesn't cite one of the offered traits.
    """
    if not missing_traits:
        return None
    prompt = (
        "A job posting wants these traits that a resume bullet doesn't mention: "
        + ", ".join(missing_traits) + ".\n\n"
        "Resume bullet:\n" + bullet + "\n\n"
        "If -- and ONLY if -- this bullet's existing achievement genuinely already "
        "demonstrates one of those traits in different words, rephrase the bullet to use "
        "the posting's vocabulary. Do NOT invent any new fact, number, date, tool, or "
        "employer. If the bullet doesn't honestly support any of those traits, reply with "
        '{"trait": null, "rewrite": null}.\n\n'
        'Reply with ONLY JSON: {"trait": "<one trait from the list, or null>", '
        '"rewrite": "<rewritten bullet, or null>"}\n\n'
        f"Job posting:\n{jd_text[:3000]}"
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = json.loads(resp.read())["message"]["content"]
        parsed = _parse_json_object(content)
        trait, rewrite = parsed.get("trait"), parsed.get("rewrite")
    except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError):
        return None
    if not trait or not rewrite or trait not in missing_traits:
        return None
    return rewrite.strip(), trait


def suggest_rewrite(bullet: str, jd_text: str, missing_traits: list[str],
                     profile: ResumeProfile, rewrite_fn: RewriteFn = local_rewrite) -> dict | None:
    """Guarded suggestion for ONE bullet. Never returns a fact-altering rewrite.

    The receipt: tailor.validate's fact-lock diff between the original and
    proposed bullet must show no added/dropped number/%/$/date -- the same
    non-negotiable guarantee tailor.py already gives resume edits. The AI's
    own citation is never trusted on its own either: a trait outside the list
    it was offered is rejected here regardless of what rewrite_fn already
    checked, because rewrite_fn is untrusted/injected (the guard boundary is
    this function, not the AI-calling one).
    """
    got = rewrite_fn(bullet, jd_text, missing_traits)
    if not got:
        return None
    rewritten, trait = got
    if not rewritten or not rewritten.strip() or trait not in missing_traits:
        return None
    result = tailor.validate(bullet, rewritten, profile)
    if not result.ok:
        return None
    return {
        "original": bullet,
        "suggested": rewritten,
        "for_trait": trait,
        "cautions": result.warnings,
    }


def suggest_all(profile: ResumeProfile, jd_text: str, missing_traits: list[str],
                 rewrite_fn: RewriteFn = local_rewrite, limit: int = 5) -> list[dict]:
    """Suggest rewrites across every resume bullet, capped at `limit` results.

    # ponytail: one AI call per bullet, sequential -- fine at resume-sized
    # bullet counts (a few dozen); parallelize only if this is measurably slow.
    """
    out: list[dict] = []
    for exp in profile.experience:
        for bullet in exp.bullets:
            if len(out) >= limit:
                return out
            s = suggest_rewrite(bullet, jd_text, missing_traits, profile, rewrite_fn)
            if s:
                out.append({**s, "company": exp.company, "title": exp.title})
    return out


def apply_bullet(profile_dict: dict, company: str, title: str, original: str,
                  suggested: str) -> bool:
    """Replace one bullet's text in a raw profile.json dict, in place.

    Operates on the raw dict (not a round-tripped ResumeProfile) so any field
    profile.json has that ResumeProfile doesn't model is left untouched.
    Only replaces an EXACT (company, title, original-bullet-text) match --
    never guesses, so a resume edited since the suggestion was made just fails
    quietly rather than touching the wrong line. Returns whether it applied.
    """
    for exp in profile_dict.get("experience", []):
        if exp.get("company") == company and exp.get("title") == title:
            bullets = exp.get("bullets", [])
            for i, b in enumerate(bullets):
                if b == original:
                    bullets[i] = suggested
                    return True
    return False
