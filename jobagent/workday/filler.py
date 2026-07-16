"""Semi-automated Workday application filler (Playwright, host-run).

WHAT IT DOES
  Drives a real browser through the WHOLE Workday application wizard:
    account (create-or-sign-in with ONE reusable email+password)
      -> My Information (name, contact, address)
      -> My Experience (resume upload, work history, education, LinkedIn)
      -> Application Questions (best-effort from your answer bank)
      -> Voluntary self-ID / EEO / disability (your stated preference, default decline)
      -> Review
  Then it STOPS on the Review page and hands control to you to submit yourself.
  It NEVER clicks the final Submit.

WHY ONE ACCOUNT EVERYWHERE
  Workday accounts are per-employer (each company is its own tenant — cookies
  never carry across), so you can't have "one login" in the literal sense. What
  you CAN do is reuse the same email + password on every tenant: the filler
  tries to sign in with them, and if no account exists yet on that employer it
  creates one with the same creds. A persistent browser profile means repeat
  applies to the SAME employer skip auth entirely.

WHY SEMI-AUTO (fill + pause, never auto-submit)
  Workday's per-employer forms and unreliable resume parser mean a blind
  auto-submit just fires off mistakes and gets accounts flagged. Filling +
  your review kills the drudgery while keeping you in control.

REQUIREMENTS (on your Windows host, NOT the container):
    pip install playwright
    playwright install chromium

Workday exposes stable `data-automation-id` attributes; we target those first
and fall back to label/role/text. Per-employer screening questions vary — the
filler logs what it answered and what it skipped so you can watch and tune.
Run it once per new employer with headless=False and watch the first time.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time

from .. import config, failpacket, guardrail, qbank, smartanswer
from .answer_bank import build_answers

PROFILE_DIR = config.ROOT / ".browser-profile"  # persistent login per host (git-ignored)
TRACE_DIR = config.ROOT / "output" / "traces"  # Playwright traces/snapshots (set JOBAGENT_TRACE=1)
DEBUG_LOG = TRACE_DIR / "apply-debug.log"  # always-on, per-line-flushed action trail

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# How long _reach_form waits for sign-in + the form to appear. Attended (you drive)
# keeps the long budget so you have time to log in / clear verification by hand;
# the unattended queue path uses a short one so a dead job fails fast and the batch
# moves on instead of freezing ~5 min per bad link.
# ponytail: 120s = room for one auto-login + one Gmail-verification round-trip. Tune
# live if a slow email trips it — a tripped job just marks 'error' and is re-addable.
_REACH_TIMEOUT_ATTENDED_S = 300
_REACH_TIMEOUT_UNATTENDED_S = 120


def _reach_timeout(auto_close: bool) -> int:
    """Reach-form budget: short & fail-fast when unattended (queue), patient when you drive."""
    return _REACH_TIMEOUT_UNATTENDED_S if auto_close else _REACH_TIMEOUT_ATTENDED_S


# Only accept verification emails newer than this (set per run in _reach_form) so a
# stale code/link from a previous employer is never reused.
_VERIFY_SINCE = 0.0
# Codes/links we've already acted on this run — stops the poll loop re-submitting a
# consumed code or re-opening a used link every 8 seconds.
_VERIFY_CONSUMED: set[str] = set()


# --------------------------------------------------------------------------- #
# Low-level, best-effort widget helpers. Every one swallows failure and returns
# a bool so the page fillers can fire a whole map at a varied form and keep going.
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    print(f"    {msg}")


def _pace() -> float:
    """Delay multiplier from JOBAGENT_SLOW (default 1.0). Workday renders async and
    over-fast actions caused mis-clicks / empty fields, so set JOBAGENT_SLOW=2 (or 3)
    to slow every wait down while a page is still finicky, then dial back. Clamped to
    [0.5, 6] so a typo can't freeze or sprint the run."""
    try:
        return max(0.5, min(6.0, float(os.environ.get("JOBAGENT_SLOW", "1"))))
    except (TypeError, ValueError):
        return 1.0


def _sleep(seconds: float) -> None:
    """time.sleep scaled by JOBAGENT_SLOW so one knob paces the whole run."""
    time.sleep(seconds * _pace())


def _visible(loc) -> bool:
    try:
        return bool(loc.count()) and loc.first.is_visible()
    except Exception:  # noqa: BLE001
        return False


def _scope(page):
    """The open modal dialog if Workday has popped one, else the page itself.

    Workday renders sign-in and 'edit this field' inside a modal layered over the
    page. The same data-automation-id frequently also exists (stale/hidden) on the
    page *behind* the modal, so an unscoped `.first` fills the wrong surface — which
    looks like 'it typed my email/password in the wrong place'. Scoping text fills
    to the visible modal puts the value where Workday is actually asking for it."""
    for sel in ('[data-automation-id="modalPopup"]',
                '[data-automation-id*="Modal"]',
                '[role="dialog"]'):
        try:
            d = page.locator(sel).last
            if d.count() and d.is_visible():
                return d
        except Exception:  # noqa: BLE001
            pass
    return page


def _front(page):
    """Follow Workday when it opens the application in a NEW tab/window: return the
    newest still-open page so we stop driving the stale original tab. No-op when
    there's only one tab.

    Does NOT call bring_to_front() by default — that raises the Chromium window and
    steals OS focus from whatever else is on screen (Ethan's game on the main
    monitor). Playwright drives a background tab fine, so focus-stealing buys nothing.
    Set JOBAGENT_FOCUS=1 if you actually want the window raised."""
    try:
        live = [p for p in page.context.pages if not p.is_closed()]
        if live and live[-1] is not page:
            if os.environ.get("JOBAGENT_FOCUS"):
                try:
                    live[-1].bring_to_front()
                except Exception:  # noqa: BLE001
                    pass
            return live[-1]
    except Exception:  # noqa: BLE001
        pass
    return page


def _dbg(msg: str) -> None:
    """Append one timestamped, immediately-flushed line to the action log. Unlike the
    trace (saved only at the end), this survives closing the window on a stuck run —
    which is why trace.zip kept not appearing. Always on; it's a tiny text file."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return  # tests exercise this code — their fake URLs in the REAL log cost a debugging session
    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _shot(page, label: str) -> None:
    """Save a full-page screenshot so Claude can SEE the actual rendered page (read the
    PNG directly) instead of inferring everything from automation-ids."""
    try:
        base = failpacket.attempt_dir() or TRACE_DIR  # per-attempt folder if one is open
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{label}.png"
        page.screenshot(path=str(path), full_page=True)
        failpacket.note_shot(label)  # so a question parked next points at this picture
        _dbg(f"screenshot -> output/traces/{path.name}")
    except Exception as exc:  # noqa: BLE001
        _dbg(f"screenshot failed: {exc}")


def _form_root(page):
    """Citi wraps the Workday wizard in an IFRAME, so page-level locators see only the
    chrome and every fill MISSes. Return the frame that actually holds the form — the
    one with the most data-automation-id nodes — falling back to the page itself.

    We count [data-automation-id] (not visible inputs): pages like My Experience have
    only Add buttons + a file-drop zone and ZERO text inputs, which made the old input
    count pick the page and miss the whole frame."""
    best, best_n = page, 0
    for fr in page.frames:
        try:
            n = fr.locator("[data-automation-id]").count()
        except Exception:  # noqa: BLE001
            n = 0
        if n > best_n:
            best, best_n = fr, n
    return best


def _dump_fields(page) -> None:
    """Log the visible inputs in EVERY frame with their real identifiers (id / name /
    aria-label / nearest wrapper automation-id), so we can see exactly which frame
    holds the fields and how to target them."""
    js = (
        "() => Array.from(document.querySelectorAll('input,select,textarea,[aria-haspopup=listbox]'))"
        ".filter(el => { const r = el.getBoundingClientRect(); return r.width>0 || r.height>0; })"
        ".map(el => { const a = el.closest('[data-automation-id]');"
        " return el.tagName.toLowerCase() + '[' + (el.type||'') + ']'"
        " + (el.id ? ' id=' + el.id : '')"
        " + (el.name ? ' name=' + el.name : '')"
        " + (el.getAttribute('aria-label') ? ' aria=' + JSON.stringify(el.getAttribute('aria-label')) : '')"
        " + (a ? ' aid=' + a.getAttribute('data-automation-id') : ''); })"
    )
    try:
        for fr in page.frames:
            try:
                items = fr.evaluate(js)
            except Exception as exc:  # noqa: BLE001 - cross-origin frames can't be evaluated
                _dbg(f"  frame [{(fr.url or '')[:55]}]: eval failed ({exc})")
                continue
            if items:
                _dbg(f"  frame [{(fr.url or '')[:55]}] inputs({len(items)}): " + " | ".join(items[:40]))
    except Exception as exc:  # noqa: BLE001
        _dbg(f"dump_fields failed ({exc})")


def _settle(page, ms: int = 6000) -> None:
    """Wait for the DOM before we act. We deliberately do NOT wait for networkidle:
    Workday is an SPA that polls forever, so networkidle NEVER fires — every call used
    to burn the full timeout (~6s each, several per page ≈ the 25s/page slowness).
    Element waits (_wait_for_fields / _wait_ready / the sig-change poll) already handle
    'is it rendered yet', so domcontentloaded + a short paint beat is enough."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=ms)
    except Exception:  # noqa: BLE001
        pass


def _wait_for_fields(page, timeout_ms: int = 20000) -> bool:
    """After a navigation/Accept, wait until real inputs actually render — Workday
    paints form fields a beat *after* the page reports 'loaded', so network-idle alone
    let us fill/dump a page that was still just chrome (all fills MISS)."""
    sel = ('[data-automation-id="legalNameSection_firstName"], '
           '[data-automation-id*="addressSection"], '
           '[data-automation-id] input[type="text"], '
           '[data-automation-id] input[type="tel"]')
    try:
        page.locator(sel).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_ready(page, *automation_ids: str, timeout_ms: int = 15000) -> bool:
    """Block (Playwright auto-wait) until one of the expected widgets is visible —
    fast when ready, patient when still loading. False on timeout so the caller can
    react instead of firing into a blank page."""
    sel = ", ".join(f'[data-automation-id="{a}"]' for a in automation_ids)
    try:
        page.locator(sel).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_gone_or_form(page, timeout_s: int = 8) -> None:
    """After submitting sign-in / create-account, wait until the form is ready OR the
    password box disappears before doing anything else — this is what stops the
    double sign-in / double account-create the fixed sleeps caused."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        _settle(page, ms=3000)
        if _form_ready(page) or not _any_visible(page, '[data-automation-id="password"]'):
            return
        time.sleep(0.5)


def _fill(page, automation_id: str, value) -> bool:
    """Fill a text input by data-automation-id (exact or *contains*).

    Picks the first VISIBLE match, not just .first — Workday's sign-in modal keeps a
    hidden Create-Account copy of the same email/password ids in the DOM, so .first
    would type into the hidden one (looks like 'credentials entered incorrectly')."""
    if value in (None, ""):
        return False
    root = _scope(page)
    for sel in (f'[data-automation-id="{automation_id}"]',
                f'[data-automation-id*="{automation_id}"]'):
        locs = root.locator(sel)
        try:
            n = min(locs.count(), 8)
        except Exception:  # noqa: BLE001
            n = 0
        for i in range(n):
            loc = locs.nth(i)
            if _visible(loc):
                try:
                    loc.fill(str(value))
                    # never echo credentials — run*.console.log files are plain text
                    shown = "*****" if "password" in automation_id.lower() else repr(str(value))
                    _log(f"set {automation_id} = {shown}")
                    _dbg(f"fill OK: {automation_id}")
                    return True
                except Exception:  # noqa: BLE001
                    continue
    _dbg(f"fill MISS: {automation_id} (had a value, no visible field matched)")
    return False


def _fill_label(page, label: str, value) -> bool:
    if value in (None, ""):
        return False
    try:
        loc = _scope(page).get_by_label(label, exact=False).first
        if _visible(loc):
            loc.fill(str(value))
            _log(f"set [{label}] = {value!r}")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _click_text(page, *texts: str, timeout_ms: int = 1500) -> bool:
    """Click the first visible button/link matching any of `texts` (exact-ish)."""
    for t in texts:
        for role in ("button", "link"):
            try:
                locs = page.get_by_role(role, name=re.compile(rf"^\s*{re.escape(t)}\s*$", re.I))
                n = min(locs.count(), 6)
            except Exception:  # noqa: BLE001
                n = 0
            for i in range(n):
                loc = locs.nth(i)
                if _visible(loc):
                    try:
                        loc.click(timeout=timeout_ms)
                        _log(f"clicked {t!r}")
                        return True
                    except Exception:  # noqa: BLE001
                        continue
    return False


def _select(page, automation_id: str, value: str) -> bool:
    """Pick `value` from a Workday dropdown/combobox (button opens a listbox)."""
    if not value:
        return False
    ctrl = page.locator(f'[data-automation-id="{automation_id}"]').first
    if not _visible(ctrl):
        return False
    try:
        ctrl.click(timeout=1500)
    except Exception:  # noqa: BLE001
        return False
    _sleep(0.4)
    # Searchable combobox: type to filter, if an input showed up. Real keystrokes —
    # a plain .fill() doesn't fire Workday's search on the searchable ones.
    try:
        box = page.locator('input[data-automation-id="searchBox"], '
                           '[data-automation-id="textInputBox"]').first
        if _visible(box):
            box.press_sequentially(value, delay=25)
            _sleep(0.5)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.locator(_OPT_SEL).first.wait_for(state="visible", timeout=4000)
    except Exception:  # noqa: BLE001
        pass
    # Click the option whose text matches (contains, case-insensitive).
    opts = page.locator(_OPT_SEL)
    seen = []
    try:
        n = opts.count()
    except Exception:  # noqa: BLE001
        n = 0
    for i in range(min(n, 60)):
        o = opts.nth(i)
        try:
            txt = (o.inner_text() or "").strip()
        except Exception:  # noqa: BLE001
            continue
        seen.append(txt)
        if value.lower() in txt.lower():
            try:
                o.click(timeout=1500)
                _log(f"selected {automation_id} = {txt!r}")
                return True
            except Exception:  # noqa: BLE001
                pass
    _dbg(f"dropdown {automation_id}: NO match for {value!r}; options={seen[:20]}")
    _log(f"(dropdown {automation_id}: no option matched {value!r})")
    return False


def _check(page, automation_id: str) -> bool:
    root = _scope(page)
    locs = root.locator(f'[data-automation-id="{automation_id}"]')
    try:
        n = min(locs.count(), 8)
    except Exception:  # noqa: BLE001
        n = 0
    for i in range(n):
        loc = locs.nth(i)
        if _visible(loc):
            try:
                loc.check()
                return True
            except Exception:  # noqa: BLE001
                try:
                    loc.click(timeout=1000)
                    return True
                except Exception:  # noqa: BLE001
                    pass
    return False


def _pick_radio_by_text(page, text: str, exact: bool = False) -> bool:
    """Click a radio/option by visible label. Prefers a real radio/option control
    (so we don't click a paragraph that merely repeats the phrase), then falls
    back to any matching text. With exact=True, anchor to the whole label so short
    answers like 'No' don't match 'November'/'Notes'."""
    if not text:
        return False
    pat = (re.compile(rf"^\s*{re.escape(text)}\s*$", re.I) if exact
           else re.compile(re.escape(text), re.I))
    for role in ("radio", "option"):
        try:
            loc = page.get_by_role(role, name=pat).first
            if _visible(loc):
                loc.click(timeout=1500)
                _log(f"radio -> {text!r}")
                return True
        except Exception:  # noqa: BLE001
            pass
    try:
        loc = page.get_by_text(pat).first
        if _visible(loc):
            loc.click(timeout=1500)
            _log(f"radio -> {text!r}")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _parse_dates(s: str):
    """'Jan 2025 - April 2026' -> ((1,2025),(4,2026)); 'Present' end -> (None,None)."""
    parts = re.split(r"\s*[–—-]\s*", s or "", maxsplit=1)
    def one(tok):
        tok = (tok or "").strip()
        if not tok or re.search(r"present|current", tok, re.I):
            return None, None
        m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s*(\d{4})", tok)
        if m:
            return _MONTHS.get(m.group(1).lower()), int(m.group(2))
        m2 = re.search(r"(\d{1,2})[/-](\d{4})", tok)
        if m2 and 1 <= int(m2.group(1)) <= 12:
            return int(m2.group(1)), int(m2.group(2))
        y = re.search(r"(\d{4})", tok)
        return (1, int(y.group(1))) if y else (None, None)
    start = one(parts[0]) if parts else (None, None)
    end = one(parts[1]) if len(parts) > 1 else (None, None)
    return start, end


def _fill_month_year(page, automation_id_contains: str, month, year, idx: int = 0) -> None:
    """Fill a Workday month/year date pair nested under the idx-th date field."""
    if not year:
        return
    scope = page.locator(f'[data-automation-id*="{automation_id_contains}"]').nth(idx)
    if not _visible(scope):
        return
    try:
        if month:
            mi = scope.locator('[data-automation-id="dateSectionMonth-input"]').first
            if _visible(mi):
                mi.fill(f"{int(month):02d}")
        yi = scope.locator('[data-automation-id="dateSectionYear-input"]').first
        if _visible(yi):
            yi.fill(str(year))
        _log(f"date {automation_id_contains} = {month or '?'}/{year}")
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Per-page fillers. Each is best-effort and a no-op if its fields aren't present,
# so the same set can be fired on every wizard page without knowing the layout.
# --------------------------------------------------------------------------- #
def _any_visible(page, selector: str) -> bool:
    """True if ANY element matching `selector` is visible — not just the first.
    Workday keeps hidden duplicates (e.g. the Create-Account password field while the
    Sign-In view is showing), so a `.first` check misjudges what's actually on screen."""
    locs = page.locator(selector)
    try:
        n = min(locs.count(), 8)
    except Exception:  # noqa: BLE001
        n = 0
    for i in range(n):
        try:
            if locs.nth(i).is_visible():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _auth_scope(page, *submit_aids: str):
    """The container that actually holds the sign-in/create submit button — modal,
    dialog, or inline form. Filling relative to THIS is what lands the credentials in
    the popup the user sees: the page behind it keeps its own visible email/password
    fields, and typing there cost a failed attempt + a 20s wait + a create-account
    detour (~40s) every run."""
    btns = ", ".join(f'[data-automation-id="{a}"]' for a in submit_aids)
    for wrap in ('[data-automation-id="modalPopup"]', '[role="dialog"]', 'form'):
        try:
            loc = page.locator(wrap, has=page.locator(btns)).last
            if loc.count() and loc.is_visible():
                return loc
        except Exception:  # noqa: BLE001
            pass
    return page


def _sign_in(page, email: str, password: str) -> bool:
    """Fill the visible sign-in fields and submit. Idempotent — safe to retry on
    every poll, e.g. after you clear an email verification and Workday re-shows the
    sign-in box. Returns False if there's no visible email field to fill."""
    if not (email and password):
        return False
    scope = _auth_scope(page, "signInSubmitButton")
    _dbg(f"sign-in scope={'page (no popup matched)' if scope is page else 'popup/form'}")
    _shot(page, "signin")  # what the sign-in view actually looked like
    if not _fill(scope, "email", email):
        return False  # no visible email field -> not a sign-in form right now
    _fill(scope, "password", password)
    _sleep(0.3)  # let Workday's onChange validation register the values before submit
    if _check_submit(scope, "signInSubmitButton"):
        _dbg("sign-in: clicked signInSubmitButton")
    elif _click_text(scope, "Sign In", "Sign in"):
        _dbg("sign-in: clicked 'Sign In' text button")
    else:  # no clickable submit found — Enter in the password field submits the form
        _dbg("sign-in: no submit button found -> Enter in password field")
        try:
            for i in range(4):
                loc = scope.locator('[data-automation-id="password"]').nth(i)
                if loc.is_visible():
                    loc.press("Enter")
                    break
        except Exception:  # noqa: BLE001
            pass
    _log("attempted sign-in")
    return True


def _create_account(page, email: str, password: str) -> None:
    """One-time: create the shared account on this employer. Workday then usually
    emails a verification code only you can enter — _reach_form keeps polling so it
    resumes (via _sign_in) the moment you're past it."""
    if not (email and password):
        print("!! No Workday credentials set. Add 'workday_email' and "
              "'workday_password' to workday_answers.json, then retry.")
        return
    _click_text(page, "Create Account")  # switch to the create view if it's a link
    # wait for the create-account view to actually render before filling, else we
    # type into the still-visible sign-in fields (or a half-painted form).
    _wait_ready(page, "verifyPassword", "createAccountSubmitButton", timeout_ms=8000)
    scope = _auth_scope(page, "createAccountSubmitButton")
    _fill(scope, "email", email)
    _fill(scope, "password", password)
    _fill(scope, "verifyPassword", password)
    _check(scope, "createAccountCheckbox")  # agree to terms, if present
    if not _check_submit(scope, "createAccountSubmitButton"):
        _click_text(scope, "Create Account")
    _log("attempted account creation — clear any email verification in the window")


def _verify_screen(page) -> bool:
    """True when Workday is asking for email verification (create-account OR a
    sign-in challenge). Specific on purpose — a visible code field or the exact
    'verification code' wording — so we don't prompt for the Gmail password on an
    ordinary job-posting/landing page."""
    if any(_any_visible(page, f'[data-automation-id="{aid}"]')
           for aid in ("verificationCode", "verificationCodeInput")):
        return True
    try:
        return page.get_by_text(re.compile("verification code", re.I)).first.is_visible(timeout=500)
    except Exception:  # noqa: BLE001
        return False


# The "account exists but you haven't verified it yet" banner (PNC: a red "Verify your
# account before you sign in..." with a Resend link, password box still up). DISTINCT
# from _verify_screen: there's no code field, and sign-in is BLOCKED until you open a
# verification LINK Workday emails. Left unhandled, the sign-in poll loops forever.
_UNVERIFIED_RE = re.compile(
    r"verify your account before you sign in"
    r"|account may need verification"
    r"|resend account (verification|activation)", re.I)


def _looks_unverified(text: str) -> bool:
    """Pure text test for the unverified-account banner — split out so it's unit-testable
    without a browser."""
    return bool(_UNVERIFIED_RE.search(text or ""))


def _account_unverified(page) -> bool:
    try:
        return page.get_by_text(_UNVERIFIED_RE).first.is_visible(timeout=500)
    except Exception:  # noqa: BLE001
        return False


def _try_email_verification(page) -> bool:
    """Auto-clear Workday's email verification by reading the OTP email from Gmail.
    If it's a CODE, type it into the visible code field and submit; if it's a LINK,
    navigate the open browser to it (allowlisted to *.workday.com hosts only).
    Returns True if it acted. No-op (returns False) without GMAIL_APP_PASSWORD."""
    from .inbox import WD_HOST_SUFFIXES, fetch_workday_verification
    found = fetch_workday_verification(newer_than_epoch=_VERIFY_SINCE)
    if not found:
        return False
    code, link = found.get("code"), found.get("link")
    # Prefer typing a code if Workday is showing a code field on this page. Try the
    # common per-tenant field ids + label wordings; each helper no-ops if absent, so
    # a whole map is safe to fire. Guard on dedup so we don't resubmit a used code.
    if code and code not in _VERIFY_CONSUMED:
        typed = (_fill_label(page, "Verification Code", code)
                 or _fill_label(page, "Enter Verification Code", code)
                 or _fill(page, "verificationCode", code)
                 or _fill(page, "verificationCodeInput", code)
                 or _fill(page, "code", code))
        if typed:
            _VERIFY_CONSUMED.add(code)
            if not (_check_submit(page, "submitButton") or _check_submit(page, "verifyButton")):
                _click_text(page, "Verify", "Submit", "Continue")
            _log(f"entered email verification code {code}")
            return True
    if link and link not in _VERIFY_CONSUMED:
        m = re.match(r"https?://([^/:]+)", link)
        host = m.group(1).lower() if m else ""
        if host and any(host == s or host.endswith("." + s) for s in WD_HOST_SUFFIXES):
            _VERIFY_CONSUMED.add(link)  # act at most once per link -> no 8s thrash loop
            _dbg(f"email verification link -> goto {link}")
            try:
                page.goto(link, wait_until="domcontentloaded")
                _log("opened email verification link")
                return True
            except Exception:  # noqa: BLE001
                pass
    return False


def _check_submit(page, automation_id: str) -> bool:
    root = _scope(page)
    locs = root.locator(f'[data-automation-id="{automation_id}"]')
    try:
        n = min(locs.count(), 6)
    except Exception:  # noqa: BLE001
        n = 0
    for i in range(n):
        loc = locs.nth(i)
        if _visible(loc):
            try:
                loc.click(timeout=2000)
                return True
            except Exception:  # noqa: BLE001
                pass
    return False


def _accept_legal_notice(page) -> bool:
    """Citi (and some Workday tenants) gate the application behind a Legal Notice /
    data-privacy page — just Accept/Decline buttons, no form fields, but it carries a
    pageFooterNextButton so it looks 'ready'. Click Accept to reveal the real form."""
    loc = page.locator('[data-automation-id="legalNoticeAcceptButton"]').first
    if not _visible(loc):
        return False
    try:
        loc.click(timeout=3000)
        _dbg("clicked Legal Notice -> Accept")
        _settle(page)
        return True
    except Exception:  # noqa: BLE001
        return False


def _form_ready(page) -> bool:
    """True once the real application wizard is on screen — the My Information
    fields or the Save-and-Continue nav — as opposed to the job posting, the
    'Start Your Application' choice screen, or a login screen."""
    return any(_any_visible(page, f'[data-automation-id="{aid}"]')
               for aid in ("legalNameSection_firstName",
                           "bottom-navigation-next-button", "pageFooterNextButton"))


_ERROR_RELOADS = 0  # reset per run in _reach_form


def _recover_if_error(page) -> bool:
    """Workday intermittently throws a 'Something went wrong / Please refresh' page — its
    own remedy is to reload, so do that (a few times max). Returns True if it saw the
    error and reloaded, so the caller re-evaluates the fresh page. If it keeps happening
    the saved draft is corrupted (usually from repeated testing) and needs a manual reset."""
    global _ERROR_RELOADS
    root = _form_root(page)  # the app (and its error screen) render inside Citi's iframe
    try:
        txt = root.locator("body").inner_text(timeout=1500)
    except Exception:  # noqa: BLE001
        return False
    if not re.search(r"something went wrong|please refresh the page", txt or "", re.I):
        return False
    if _ERROR_RELOADS >= 3:
        print("\nWorkday keeps showing 'Something went wrong'. The saved draft is likely "
              "corrupted (repeated test runs) — in the browser go to Candidate Home, "
              "WITHDRAW this in-progress application, then start it fresh (or apply to a "
              "different posting). The autofill will build it cleanly from an empty draft.")
        _dbg("error page persists after 3 reloads — giving up")
        return False
    _ERROR_RELOADS += 1
    _dbg(f"error page -> reload {_ERROR_RELOADS}")
    print(f"Workday error page — refreshing (try {_ERROR_RELOADS})…")
    try:
        page.reload(wait_until="domcontentloaded")
    except Exception:  # noqa: BLE001
        pass
    _settle(page)
    _sleep(1.0)
    return True


def _reach_form(page, email: str, password: str, url: str = "", timeout_s: int = 300) -> bool:
    """Click through Workday's Apply / 'Apply Manually' gateway and sign-in until
    the application form actually appears.

    Polls instead of blocking on input(), so YOU can clear an email verification or
    click through in the open window and the filler resumes the instant the form (or
    the sign-in box) shows. Signs in on EVERY pass — so it re-signs-in after you
    finish verifying a freshly created account — and creates the account at most once.
    """
    print("Reaching the application form. Sign-in, 'Apply Manually' / 'Autofill with "
          "Resume', and email verification are handled automatically — the Gmail app "
          "password is asked once (hidden), then saved encrypted so you're not asked again.")
    global _VERIFY_SINCE
    _VERIFY_SINCE = time.time() - 120  # accept emails from just before this run started
    _VERIFY_CONSUMED.clear()  # fresh dedup state each run
    global _ERROR_RELOADS
    _ERROR_RELOADS = 0
    deadline = time.time() + timeout_s
    tried_create = False
    requested_verify = False
    home_recoveries = 0
    login_hops = 0
    last_email_check = 0.0
    while time.time() < deadline:
        page = _front(page)  # follow Workday if it opened a new tab
        _settle(page)  # never judge the page until it's actually done loading
        ready = _form_ready(page)
        pw = _any_visible(page, '[data-automation-id="password"]')
        _dbg(f"reach_form: url={page.url} ready={ready} pw_box={pw}")
        if ready:
            return True
        # Account exists but isn't verified yet (PNC's "Verify your account before you
        # sign in" banner — password box still up, so the sign-in path below would just
        # loop forever). Request ONE fresh verification email, then open the LINK it
        # sends via the same email path. Checked before sign-in because pw is True here.
        if _account_unverified(page):
            if not requested_verify:
                _click_text(page, "Resend Account Verification", "Resend Account Activation",
                            "Resend Verification")
                requested_verify = True
                _log("account not verified yet -> requested a fresh verification email")
                _sleep(1.5)  # give Workday a beat to send it
            if time.time() - last_email_check > 8:  # throttle IMAP; email takes a few s
                last_email_check = time.time()
                if _try_email_verification(page):  # opens the emailed activate LINK
                    _wait_gone_or_form(page)
            _sleep(1.0)
            continue
        # Stuck on a verification step (either after creating the account, or a
        # sign-in email challenge): pull the code/link from Gmail. Throttled so we
        # don't hammer IMAP, and gated on an actual verify screen so we never prompt
        # for the Gmail password on an ordinary landing page.
        if (tried_create or _verify_screen(page)) and not pw \
                and time.time() - last_email_check > 8:
            last_email_check = time.time()
            if _try_email_verification(page):
                _wait_gone_or_form(page)
                continue
        if pw:
            # A sign-in box is showing. Sign in, then WAIT for the result (form shows
            # or the box clears) before acting again — fixed sleeps here are what made
            # it submit twice / create the account twice.
            if _click_text(page, "Accept Cookies"):
                _dbg("dismissed cookie banner")
                _sleep(0.5)
            if "/login" not in (page.url or "") and login_hops < 2:
                # The INLINE sign-in card on .../apply/applyManually silently swallows
                # the submit — four live runs failed there and then succeeded first-try
                # on /login every time. Sign in on the tenant's real login page; the
                # Candidate-Home recovery below returns us to the posting afterwards.
                m = re.match(r"(https://[^/]+(?:/[a-z]{2}-[A-Z]{2})?/[^/]+)/", page.url or "")
                if m:
                    login_hops += 1
                    login_url = m.group(1) + "/login"
                    _dbg(f"sign-in card on apply page is unreliable -> goto {login_url}")
                    try:
                        page.goto(login_url, wait_until="domcontentloaded")
                    except Exception:  # noqa: BLE001
                        pass
                    _settle(page)
                    continue
            _dbg("sign-in box visible -> attempting sign-in")
            _sign_in(page, email, password)
            _wait_gone_or_form(page)
            page = _front(page)
            if _form_ready(page):
                return True
            if _any_visible(page, '[data-automation-id="password"]') and not tried_create:
                _dbg("still a sign-in box -> creating the account (once)")
                _create_account(page, email, password)
                tried_create = True
                _wait_gone_or_form(page)
        else:
            # Job posting or the 'Start Your Application' choice screen: click in.
            # 'Apply Manually' is tried first so we don't trigger resume-autofill.
            # Only re-loop after the click has had a chance to navigate.
            clicked = _click_text(page, "Apply Manually", "Apply", "Start Your Application")
            _dbg(f"reach_form: no form/pw on this page -> apply-button clicked={clicked}")
            if clicked:
                _settle(page)
            elif url and home_recoveries < 3 and "userhome" in (page.url or "").lower():
                # Signed in, but Workday dropped us on Candidate Home with no Apply
                # button — the dead-end the debug log showed. Re-open the posting's
                # apply route directly; now that we're authenticated it loads the form
                # instead of the sign-in box.
                home_recoveries += 1
                apply_url = url.rstrip("/") + "/apply/applyManually"
                _dbg(f"reach_form: stuck on Candidate Home -> goto {apply_url} (try {home_recoveries})")
                try:
                    page.goto(apply_url, wait_until="domcontentloaded")
                except Exception:  # noqa: BLE001
                    pass
                _settle(page)
        _sleep(1.0)
    _dbg(f"reach_form: TIMED OUT, final url={page.url} ready={_form_ready(page)}")
    return _form_ready(page)


def _fill_any(root, value, *css_selectors) -> bool:
    """Fill the first VISIBLE input matching any of the CSS selectors. One call covers
    both legacy and modern Workday field naming (the ids differ wildly per tenant)."""
    if value in (None, ""):
        return False
    for css in css_selectors:
        locs = root.locator(css)
        try:
            n = min(locs.count(), 8)
        except Exception:  # noqa: BLE001
            n = 0
        for i in range(n):
            loc = locs.nth(i)
            if _visible(loc):
                try:
                    loc.fill(str(value))
                    _dbg(f"fill OK: {css}")
                    return True
                except Exception:  # noqa: BLE001
                    continue
    _dbg(f"fill MISS: {css_selectors[0]} (+{len(css_selectors)-1} alts)")
    return False


_OPT_SELS = ('[data-automation-id="promptOption"]',
             '[data-automation-id="promptLeafNode"]',
             '[data-automation-id="activeListItem"]',
             '[role="option"]',
             '[data-automation-id="menuItem"]')


def _keyboard(root):
    """Playwright keyboard for a frame OR a page (Frame.page -> Page; Page has no
    .page so it returns itself). Workday dropdowns filter on keystrokes, so we need
    to type into whatever currently has focus."""
    return getattr(root, "page", root).keyboard


def _match_option(root, value: str) -> bool:
    """Click the first visible listbox option whose text contains `value`."""
    needle = value.lower()
    for opt_sel in _OPT_SELS:
        opts = root.locator(opt_sel)
        try:
            n = min(opts.count(), 80)
        except Exception:  # noqa: BLE001
            n = 0
        for i in range(n):
            o = opts.nth(i)
            try:
                if not o.is_visible():
                    continue
                txt = " ".join((o.inner_text() or "").split()).strip()
            except Exception:  # noqa: BLE001
                continue
            if needle in txt.lower():
                try:
                    o.click(timeout=1500)
                    _dbg(f"select OK: {value!r} -> {txt!r}")
                    return True
                except Exception:  # noqa: BLE001
                    pass
    return False


def _dump_options(root) -> None:
    """Log the option labels currently rendered in an open dropdown, so a MISS tells
    us the tenant's REAL option text (e.g. Citi's 'How did you hear' taxonomy) instead
    of leaving us guessing the exact phrasing."""
    for opt_sel in _OPT_SELS:
        opts = root.locator(opt_sel)
        try:
            n = min(opts.count(), 50)
        except Exception:  # noqa: BLE001
            n = 0
        texts = []
        for i in range(n):
            try:
                o = opts.nth(i)
                if o.is_visible():
                    texts.append(" ".join((o.inner_text() or "").split())[:45])
            except Exception:  # noqa: BLE001
                pass
        if texts:
            _dbg(f"  open-options[{opt_sel}] ({len(texts)}): " + " | ".join(texts))


def _chip_count(root) -> int:
    """Visible selected-value pills. Workday multiselects render a
    [data-automation-id="selectedItem"] chip per pick, so a rising count is proof a
    choice actually committed — a category-click that only expands a subtree adds none."""
    try:
        c = root.locator('[data-automation-id="selectedItem"]')
        return sum(1 for i in range(min(c.count(), 12)) if c.nth(i).is_visible())
    except Exception:  # noqa: BLE001
        return 0


def _select2(root, value, *control_css, label=None) -> bool:
    """Open a Workday dropdown/combobox — by CSS control selector or visible label —
    and click the option matching `value`.

    Workday VIRTUALIZES long lists (State, Country): only ~a dozen options are in the
    DOM until you type to filter, so a plain scan never finds 'New Jersey'. We type the
    value (into a search box if one shows, else via the keyboard, since the open list
    captures keystrokes), wait for the filtered options to render, then click the match.
    On a miss we dump the rendered option labels so the next iteration learns the real
    text."""
    if not value:
        return False
    ctrl = None
    for css in control_css:
        loc = root.locator(css).first
        if _visible(loc):
            ctrl = loc
            break
    if ctrl is None and label:
        try:
            loc = root.get_by_label(re.compile(label, re.I)).first
            if _visible(loc):
                ctrl = loc
        except Exception:  # noqa: BLE001
            pass
    if ctrl is None:
        _dbg(f"select MISS: no control for {value!r} ({control_css or label})")
        return False
    # Already showing the desired value (e.g. Country pre-defaults to US)? Don't reopen.
    try:
        cur = " ".join((ctrl.inner_text() or "").split())
        if cur and value.lower() in cur.lower():
            _dbg(f"select skip: {value!r} already set ({cur!r})")
            return True
    except Exception:  # noqa: BLE001
        pass
    # Multiselects (an <input> control: source, phone code) commit by adding a chip;
    # button dropdowns (State, Country) just show the value in the button. Track chips
    # so we can tell a real commit from a category that merely expanded.
    try:
        is_multi = (ctrl.evaluate("el => el.tagName") or "").lower() == "input"
    except Exception:  # noqa: BLE001
        is_multi = False
    before_chips = _chip_count(root) if is_multi else 0
    try:
        ctrl.click(timeout=1500)
    except Exception:  # noqa: BLE001
        return False
    _sleep(0.5)
    # A 'A > B' value walks a hierarchical prompt (Citi's How-Did-You-Hear is
    # Website > Citi Jobs Career Site): click each level in turn. A single value is one
    # step. Don't type-to-filter a multi-step path (the full string filters to nothing).
    steps = [s.strip() for s in str(value).split(">") if s.strip()] or [str(value)]
    if len(steps) == 1:
        # Type to filter — a dedicated search box if Workday renders one, else straight
        # to the focused list (the open list captures keystrokes). Narrows virtualized
        # lists (State) down to the match.
        typed = False
        try:
            box = root.locator('input[data-automation-id="searchBox"], '
                               'input[data-automation-id="textInputBox"], '
                               '[data-automation-id="promptSearchBox"] input').first
            if _visible(box):
                box.fill(steps[0])
                typed = True
                _sleep(0.5)
        except Exception:  # noqa: BLE001
            pass
        if not typed:
            try:
                _keyboard(root).type(steps[0], delay=15)
                _sleep(0.6)
            except Exception:  # noqa: BLE001
                pass
    # Walk the path. NOTE: never press Escape after a pick — on Citi's input-multiselects
    # Escape CANCELS the just-added value (silently empties a required field, blocks Save).
    for step in steps:
        deadline = time.time() + 2.5 * _pace()
        hit = False
        while time.time() < deadline:
            if _match_option(root, step):
                hit = True
                break
            _sleep(0.3)
        if not hit:
            _dbg(f"select MISS: no option {step!r}" + (f" in path {value!r}" if len(steps) > 1 else ""))
            _dump_options(root)
            try:
                _keyboard(root).press("Escape")
            except Exception:  # noqa: BLE001
                pass
            return False
        _sleep(0.4)
    if not is_multi:
        return True
    # Multiselect: a chip must have landed, else the last step only expanded a subtree.
    if _chip_count(root) > before_chips:
        _dbg(f"select OK (chip): {value!r}")
        return True
    _dbg(f"select expanded but no chip for {value!r} — revealed leaves:")
    _dump_options(root)
    return False


# 'How did you hear about us' is non-material, but honesty still applies: prefer a real
# neutral channel and NEVER auto-claim a referral/recruiter the user didn't have.
_SOURCE_NEUTRAL = ("website", "career site", "careers", "company", "job board", "indeed",
                   "linkedin", "search engine", "google", "online", "social", "other")
_SOURCE_AVOID = ("referral", "employee", "recruiter", "agency", "colleague", "friend")


def _pick_source_text(option_texts, want=(), exclude=()):
    """Choose which visible option label to click. PURE (unit-tested): a `want` term match
    (from a configured value) wins; else a neutral channel; else the first option that
    isn't a referral/recruiter; else — only if that's all there is — the first option.
    `exclude` skips labels already clicked this open (stops re-clicking a category)."""
    texts = [t for t in option_texts if t and t not in exclude]
    low = [(t, t.lower()) for t in texts]
    for w in (w.lower() for w in want if w):
        for t, tl in low:
            if w in tl:
                return t
    for n in _SOURCE_NEUTRAL:
        for t, tl in low:
            if n in tl:
                return t
    for t, tl in low:
        if not any(a in tl for a in _SOURCE_AVOID):
            return t
    return texts[0] if texts else None


def _source_chip_count(root) -> int:
    """selectedItem chips WITHIN the source field only. A global _chip_count also counts
    the phone country-code multiselect's chip (+1), which false-reads as 'source answered'
    and skips the field — the exact bug that left How-Did-You-Hear empty on PNC."""
    try:
        c = root.locator('[data-automation-id="formField-source"] '
                         '[data-automation-id="selectedItem"]')
        return sum(1 for i in range(min(c.count(), 6)) if c.nth(i).is_visible())
    except Exception:  # noqa: BLE001
        return 0


def _click_source_option(root, want=(), clicked=None) -> bool:
    """Click one option in the open source prompt, chosen by _pick_source_text. Gathers the
    visible leaf/category labels, picks the best (skipping any already clicked this open),
    clicks it. A category expands (leaves show next pass); a leaf commits a chip."""
    seen = {}
    for sel in ('[data-automation-id="promptLeafNode"]',
                '[data-automation-id="promptOption"]', '[role="option"]'):
        opts = root.locator(sel)
        try:
            n = min(opts.count(), 40)
        except Exception:  # noqa: BLE001
            n = 0
        for i in range(n):
            o = opts.nth(i)
            try:
                if not o.is_visible():
                    continue
                txt = " ".join((o.inner_text() or "").split())
            except Exception:  # noqa: BLE001
                continue
            if txt and txt not in seen:
                seen[txt] = o
    pick = _pick_source_text(list(seen), want, exclude=clicked or ())
    if pick and pick in seen:
        try:
            seen[pick].click(timeout=1500)
            if clicked is not None:
                clicked.add(pick)
            _dbg(f"source: clicked {pick!r}")
            return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _fill_source(root, preferred=None, *, sels=(
        '#source--source',
        '[data-automation-id="formField-source"] [data-automation-id="multiselectInputContainer"]',
        '[data-automation-id="formField-source"] input',
        '[data-automation-id="formField-source"]')) -> bool:
    """'How Did You Hear About Us?' — a per-EMPLOYER required picklist (Citi = 'Website >
    Citi Jobs Career Site', PNC = 'Corporate Website > PNC Career Site'), so no hardcoded
    value works everywhere. Open the widget and drill to a real leaf, steering toward the
    configured value's terms where they line up. Returns True once a chip lands. Logs every
    exit — never bails silently (the old version did, which left the field empty on PNC)."""
    ctrl = next((loc for css in sels if _visible(loc := root.locator(css).first)), None)
    if ctrl is None:
        _dbg("source: no visible control")
        return False
    if _source_chip_count(root):  # scoped to the source field, NOT the phone-code chip
        _dbg("source: already answered")
        return True
    want = [s.strip() for s in str(preferred or "").split(">") if s.strip()]
    for attempt in range(3):
        try:
            ctrl.click(timeout=2000)  # open (or re-open) the prompt
        except Exception as exc:  # noqa: BLE001
            _dbg(f"source: couldn't open widget ({exc}) — retry {attempt + 1}")
            _sleep(0.6)
            continue
        _sleep(0.6)
        clicked = set()  # don't re-click a category we already drilled into this open
        for _ in range(6):  # drill category -> leaf until a chip commits
            if _source_chip_count(root):
                _dbg("source OK: option committed")
                return True
            if not _click_source_option(root, want, clicked):
                break
            _sleep(0.4)
        if _source_chip_count(root):
            _dbg("source OK: option committed")
            return True
        try:  # nothing landed — close and try a fresh open
            _keyboard(root).press("Escape")
        except Exception:  # noqa: BLE001
            pass
        _sleep(0.4)
    _dbg("source: could not commit any option after retries")
    _dump_options(root)
    return False


def _pick_radio_in(root, container_aid: str, option_text: str) -> bool:
    """Click a radio option by label WITHIN one question's container, so 'No' targets
    that question and not some other 'No' elsewhere on the page."""
    try:
        cont = root.locator(f'[data-automation-id="{container_aid}"]').first
        if not _visible(cont):
            return False
        pat = re.compile(rf"^\s*{re.escape(option_text)}\s*$", re.I)
        opt = cont.get_by_role("radio", name=pat).first
        if not _visible(opt):
            opt = cont.get_by_text(pat).first
        if _visible(opt):
            opt.click(timeout=1500)
            _dbg(f"radio OK: {container_aid} -> {option_text}")
            return True
    except Exception as exc:  # noqa: BLE001
        _dbg(f"radio MISS: {container_aid} ({exc})")
    return False


def _fill_my_information(page, ans: dict) -> None:
    # Citi runs a NEWER Workday: fields are named formField-* / name="legalName--*" /
    # name="addressLine1" etc. — nothing like the legacy legalNameSection_* ids. Try
    # modern selectors first, keep the legacy ones as fallbacks for other tenants.
    _fill_any(page, ans.get("first_name"),
              'input[name="legalName--firstName"]',
              '[data-automation-id="formField-legalName--firstName"] input',
              '[data-automation-id="legalNameSection_firstName"]')
    _fill_any(page, ans.get("last_name"),
              'input[name="legalName--lastName"]',
              '[data-automation-id="formField-legalName--lastName"] input',
              '[data-automation-id="legalNameSection_lastName"]')
    _fill_any(page, ans.get("address_line1"),
              'input[name="addressLine1"]',
              '[data-automation-id="formField-addressLine1"] input',
              '[data-automation-id="addressSection_addressLine1"]')
    _fill_any(page, ans.get("city"),
              'input[name="city"]',
              '[data-automation-id="formField-city"] input',
              '[data-automation-id="addressSection_city"]')
    _fill_any(page, ans.get("postal_code"),
              'input[name="postalCode"]',
              '[data-automation-id="formField-postalCode"] input',
              '[data-automation-id="addressSection_postalCode"]')
    _fill_any(page, ans.get("phone"),
              'input[name="phoneNumber"]',
              '[data-automation-id="formField-phoneNumber"] input',
              '[data-automation-id="phone-number"]')
    _fill(page, "email", ans.get("email"))  # display-only on Citi; harmless elsewhere

    # "Have you ever been employed by Citi...?" Yes/No -> No (scoped to that question).
    _pick_radio_in(page, "formField-candidateIsPreviousWorker", "No")

    # Dropdowns — modern formField-* ids, legacy ids, then the visible label as a net.
    # Dropdowns. Citi has TWO widget kinds: input-multiselects (source, phone code)
    # that render promptLeafNode options, and BUTTON dropdowns (Country, State, Phone
    # Device Type) — for those target the exact <button> id (#address--countryRegion),
    # since the formField-* wrapper isn't the clickable element.
    # 'How Did You Hear About Us?' — per-employer required picklist; use the configured
    # value if it matches this tenant, else take the first available option. (Was a Citi-
    # only 'Website > Citi Jobs Career Site' that could never match PNC/others.)
    _fill_source(page, preferred=ans.get("how_did_you_hear"))
    _select2(page, ans.get("country", "United States of America"),
             '#country--country', '[data-automation-id="formField-country"]',
             '[data-automation-id="countryDropdown"]', label="Country")
    _select2(page, ans.get("state"),
             '#address--countryRegion', '[data-automation-id="formField-countryRegion"]',
             '[data-automation-id="addressSection_countryRegion"]', label="State")
    _select2(page, ans.get("phone_device_type", "Mobile"),
             '#phoneNumber--phoneType', '[data-automation-id="formField-phoneType"]',
             '[data-automation-id="phoneType"]', label="Phone Device Type")


def _fill_card(root, automation_id: str, idx: int, value) -> bool:
    """Fill the idx-th field with an EXACT data-automation-id, on the frame directly
    (NOT via _scope). The repeating work/education cards put the aid on the input
    itself (e.g. formField-jobTitle on Work Experience N), so .nth(idx).fill() lands in
    the right card — same approach as the working date helper. A _scope-routed
    variant missed these (removed)."""
    if value in (None, ""):
        return False
    # ASCII-safe, one-line preview for logging (the bulleted description has newlines +
    # a '•' that would crash a cp1252 console — this project's known encoding gotcha).
    shown = str(value).replace("\n", " ").encode("ascii", "replace").decode()[:50]
    loc = root.locator(f'[data-automation-id="{automation_id}"]').nth(idx)
    try:
        if not loc.count():
            return False
        loc.fill(str(value))  # aid is on the input/textarea itself
        _log(f"set {automation_id}[{idx}] = {shown!r}")
        return True
    except Exception:  # noqa: BLE001 - some aids wrap the input; try the inner control
        try:
            loc.locator("input, textarea").first.fill(str(value))
            _log(f"set {automation_id}[{idx}] = {shown!r}")
            return True
        except Exception:  # noqa: BLE001
            return False


# VISIBLE options only — Workday keeps every prompt's option list in the DOM (hidden
# until that prompt opens), so an unscoped read pulls the wrong list (e.g. the big
# Field-of-Study list showed up for School AND Degree). ':visible' restricts to the one
# open popup. Multiple option shapes because Field-of-Study uses promptOption but School
# (radio results) and Degree (menu) render differently.
_OPT_SEL = ('[data-automation-id="promptOption"]:visible, [role="option"]:visible, '
            '[data-automation-id="menuItem"]:visible, [data-automation-id="promptLeafNode"]:visible, '
            '[role="radio"]:visible, [role="treeitem"]:visible, '
            '[data-automation-id="promptItem"]:visible, [data-automation-id="checkboxItem"]:visible')


def _dump_option_candidates(root, tag: str) -> None:
    """On a prompt miss, log the visible option-ish elements (role/aid/text) so we can see
    exactly how this widget renders its options and target them precisely."""
    js = r"""() => Array.from(document.querySelectorAll('[role],[data-automation-id],option,li,label'))
        .filter(el => { const r = el.getBoundingClientRect(); return (r.width > 0 || r.height > 0); })
        .filter(el => { const role = (el.getAttribute('role')||'').toLowerCase();
                        const aid = el.getAttribute('data-automation-id')||'';
                        const t = el.tagName.toLowerCase();
                        const txt = (el.innerText||'').trim();
                        return txt && txt.length < 50 &&
                          (t === 'option' || t === 'label' ||
                           /option|radio|listitem|menuitem|treeitem/.test(role) ||
                           /prompt|option|menuitem|leaf|listitem|checkbox|result|item/i.test(aid)); })
        .slice(0, 40)
        .map(el => (el.getAttribute('role') || el.tagName.toLowerCase()) + '|aid=' +
                   (el.getAttribute('data-automation-id')||'') + '|' +
                   (el.innerText||'').trim().slice(0, 28))"""
    try:
        items = root.evaluate(js)
        _dbg(f"option candidates [{tag}] ({len(items)}): " + " ;; ".join(items[:40]))
    except Exception as e:  # noqa: BLE001
        _dbg(f"option candidates [{tag}] dump failed: {e}")


def _degree_aliases(value: str):
    """Degree picklists differ by employer: Citi uses abbreviations (B.S., B.A., M.S.),
    PNC uses plural level words (Bachelors, Masters, Associates). Return BOTH forms so the
    substring matcher lands on whichever the tenant offers. The plural level word
    ('bachelors') matches PNC's 'Bachelors' but NOT a spelled-out 'Bachelor of Arts/Science'
    split (those are singular) — so we never pick the wrong BA-vs-BS subtype. The level is
    always accurate: a B.S. IS a bachelor's. MBA and JD come FIRST: an MBA routed to the
    M.S. forms clicked "Master of Science" (a degree not held — the honesty-contract class),
    and 'Juris Doctor' contains 'doctor' so it fell through to the Ph.D. forms."""
    v = (value or "").lower()
    if "mba" in v or "m.b.a" in v or "business administration" in v:
        return ["MBA", "M.B.A.", "Master of Business Administration", "masters"]
    if "juris" in v or "j.d" in v or v.strip() == "jd":
        return ["J.D.", "JD", "Juris Doctor"]
    if "bachelor" in v and "art" in v:
        return ["B.A.", "Bachelor of Arts", "bachelors"]
    if "bachelor" in v or "b.s" in v or "bsc" in v:
        return ["B.S.", "BSc", "B.Sc", "Bachelor of Science", "bachelors"]
    if "master" in v and "art" in v:
        return ["M.A.", "Master of Arts", "masters"]
    if "master" in v or "m.s" in v or "msc" in v:
        return ["M.S.", "MSc", "M.Sc", "Master of Science", "masters"]
    if "associate" in v:
        return ["Associate", "associates"]
    if "phd" in v or "ph.d" in v or "doctor" in v:
        return ["Ph.D.", "PhD", "Doctorate", "doctoral"]
    return []


def _pick_prompt(root, automation_id: str, value: str, idx: int = 0, aliases=()) -> bool:
    """Select from a Workday prompt (School, Degree, Field of Study). These open a popup
    with a scrollable option list and a 'Search' box at the bottom. So: open it, type the
    query into that Search box (found by its visible placeholder, which is stable), then
    POLL for the matching option and click it — polling because School's search hits a
    remote school DB and the results arrive async. `aliases` are extra acceptable option
    texts (e.g. Degree 'B.S.' for 'Bachelor of Science'). Logs offered options on a miss."""
    if not value:
        return False
    ctrl = root.locator(f'[data-automation-id="{automation_id}"]').nth(idx)
    try:
        if not ctrl.count():
            return False
        try:  # close any popup a previous prompt left open, so only this one is visible
            root.locator("body").press("Escape")
            _sleep(0.2)
        except Exception:  # noqa: BLE001
            pass
        try:
            ctrl.scroll_into_view_if_needed(timeout=1500)
        except Exception:  # noqa: BLE001
            pass
        # The aid can sit ON the control (School: the input) or on a WRAPPER around it
        # (Degree: a div around the 'Select One' button) — clicking the wrapper lands on
        # dead space and no popup opens (live run: focus stayed on DIV#mainContent). So
        # click the button/input INSIDE the aid when the aid element isn't one itself.
        target = ctrl
        try:
            tag = (ctrl.evaluate("el => el.tagName.toLowerCase()") or "")
        except Exception:  # noqa: BLE001
            tag = ""
        try:  # already showing our value (saved draft) — re-opening could clear it
            cur = ctrl.input_value(timeout=500) if tag == "input" else (ctrl.inner_text() or "")
            probes = [value.lower()[:12]] + [a.lower() for a in aliases]
            if cur and any(p and p in cur.lower() for p in probes):
                _dbg(f"prompt {automation_id}: already set ({cur.strip()[:40]!r}) — skip")
                return True
        except Exception:  # noqa: BLE001
            pass
        if tag not in ("button", "input"):
            inner = ctrl.locator("button, input").first
            try:
                if inner.count():
                    target = inner
            except Exception:  # noqa: BLE001
                pass
        try:
            target.click(timeout=2500)  # open the popup
        except Exception:  # noqa: BLE001
            ctrl.click(timeout=2000)
        _sleep(0.4)
        # Where to type: prefer the control's OWN <input> (School is a typeahead — typing
        # into its own input cannot land in another field; a live run showed blind
        # keyboard strokes going nowhere, School's box left empty). Keyboard remains the
        # fallback for popup-style prompts whose search box takes focus on open. We still
        # never hunt for a search box BY SELECTOR — every prompt keeps one in the DOM, so
        # that used to type the School name into Field of Study's box.
        # Degree (aliases) is a plain dropdown with no search — just scan the list.
        kb = box = None
        if not aliases:
            try:
                if tag == "input":
                    box = ctrl
                else:
                    inner = ctrl.locator("input").first
                    if inner.count() and inner.is_visible():
                        box = inner
            except Exception:  # noqa: BLE001
                box = None
            try:
                kb = root.page.keyboard
            except Exception:  # noqa: BLE001
                kb = None
        head = " ".join(value.split()[:2]).lower()
        wants = [value.lower(), head] + [a.lower() for a in aliases]

        scopes = [root]
        try:  # popups can PORTAL outside the form iframe — scan the top page too
            if root.page is not root:
                scopes.append(root.page)
        except Exception:  # noqa: BLE001
            pass

        def _find_and_click():
            """Scroll the current result list and click the first option matching wants.
            Returns (clicked, options_seen)."""
            seen_here, dedup = [], set()
            for sc in scopes:
                opts = sc.locator(_OPT_SEL)
                try:
                    n = opts.count()
                except Exception:  # noqa: BLE001
                    n = 0
                for i in range(min(n, 300)):
                    o = opts.nth(i)
                    try:
                        txt = (o.inner_text() or "").strip()
                    except Exception:  # noqa: BLE001
                        continue
                    if not txt or txt in dedup:
                        continue
                    dedup.add(txt)
                    seen_here.append(txt)
                    if any(w and w in txt.lower() for w in wants):
                        try:
                            o.scroll_into_view_if_needed(timeout=1000)
                            o.click(timeout=2000)
                            return True, seen_here
                        except Exception:  # noqa: BLE001
                            pass
                # nothing matched in what's rendered — scroll the last one into view to
                # pull more (Ethan's "scroll through the results and see what matches").
                if n:
                    try:
                        opts.nth(min(n, 300) - 1).scroll_into_view_if_needed(timeout=800)
                    except Exception:  # noqa: BLE001
                        pass
            return False, seen_here

        # Ethan's manual flow: type the first few letters, press ENTER to run the search
        # (School is a remote DB lookup that only executes on Enter — that's why it came
        # back empty before), then scroll the results and pick the match. Refine with a
        # few more letters + Enter if the first pass doesn't surface it.
        seen = []
        for chunk in (4, 7, 10, len(value)):  # widen the query if needed
            if box is not None:
                try:
                    box.click(timeout=1500)
                    box.fill("")  # clear any prior/partial query
                    box.press_sequentially(value[:chunk], delay=40)
                    _sleep(0.3)
                    box.press("Enter")
                except Exception:  # noqa: BLE001
                    pass
                _sleep(1.0)  # let results populate (async for School)
            elif kb is not None:
                try:
                    kb.type(value[:chunk], delay=40)
                    _sleep(0.3)
                    kb.press("Enter")
                except Exception:  # noqa: BLE001
                    pass
                _sleep(1.0)
            deadline = time.time() + 5
            while time.time() < deadline:
                clicked, seen = _find_and_click()
                if clicked:
                    _log(f"prompt {automation_id} -> matched (typed {min(chunk, len(value))})")
                    return True
                _sleep(0.5)
            if (box is None and kb is None) or chunk >= len(value):
                break
            if box is None:
                try:  # clear the box before retyping a longer query
                    for _ in range(chunk + 2):
                        kb.press("Backspace")
                except Exception:  # noqa: BLE001
                    pass
        try:  # where did focus (and any typed text) actually land? — key diagnostic
            ae = root.evaluate(
                "() => { const e = document.activeElement; return e ? "
                "e.tagName + '#' + (e.id||'') + ' aid=' + (e.getAttribute('data-automation-id')||'')"
                " + ' val=' + String(e.value||'').slice(0,30) : 'none'; }")
            _dbg(f"prompt {automation_id}: focus={ae}")
        except Exception:  # noqa: BLE001
            pass
        for sc in scopes:  # reveal how THIS widget renders options (frame AND top page)
            _dump_option_candidates(sc, automation_id)
        _dbg(f"prompt {automation_id}: NO match for {value!r}; options={seen[:25]}")
        _log(f"(prompt {automation_id}: no option matched {value!r})")
        return False
    except Exception as e:  # noqa: BLE001
        _dbg(f"prompt {automation_id} error: {e}")
        return False


def _card_count(root, automation_id: str) -> int:
    try:
        return root.locator(f'[data-automation-id="{automation_id}"]').count()
    except Exception:  # noqa: BLE001
        return 0


def _delete_extra_cards(root, count_aid: str, keep: int, preceding_aids=()) -> None:
    """Trim a repeating section down to `keep` cards, undoing accumulation from prior
    runs (Workday saves the draft, so re-runs pile up empty cards that block Submit).
    Card 'Delete' buttons appear in DOM/section order, so the last card's Delete is at
    index (cards in sections above) + (cards here) - 1. Best-effort."""
    guard = 0
    while _card_count(root, count_aid) > keep and guard < 15:
        guard += 1
        before = sum(_card_count(root, a) for a in preceding_aids)
        del_index = before + _card_count(root, count_aid) - 1
        dels = root.get_by_role("button", name=re.compile(r"Delete", re.I))
        if dels.count() <= del_index:
            dels = root.get_by_role("link", name=re.compile(r"Delete", re.I))
        if dels.count() <= del_index:
            break
        try:
            tgt = dels.nth(del_index)
            tgt.scroll_into_view_if_needed(timeout=1000)
            tgt.click(timeout=1500)
            _sleep(0.4)
            _click_text(root, "OK", "Yes")  # dismiss a confirm dialog if one appears
        except Exception:  # noqa: BLE001
            break


def _fill_experience(page, ans: dict) -> None:
    # This fires on every wizard page; do the heavy work once so we never re-upload
    # the resume or duplicate work/education entries on later pages.
    if ans.get("_experience_done"):
        return

    # Resume upload — short-circuit instantly when there's no file input on this
    # page (else set_input_files auto-waits ~30s every page), and prefer a real
    # resume input so a multi-input page doesn't strict-mode error. The file input is
    # ONLY on My Experience, so a successful upload also tells us we're on that page —
    # don't mark the step done until then, or earlier pages would skip it.
    resume = ans.get("resume_file", "")
    uploaded = False
    if resume:
        # Don't re-upload if the file is already attached — re-running against a saved
        # Workday draft otherwise stacks duplicate copies of the resume on Review.
        base = os.path.basename(resume)
        try:
            if base and page.get_by_text(base, exact=False).count() > 0:
                _dbg("resume already attached — skipping upload")
                uploaded = True
        except Exception:  # noqa: BLE001
            pass
        if not uploaded:
            for sel in ('input[type="file"][data-automation-id*="file-upload-input"]',
                        '[data-automation-id*="resume"] input[type="file"]',
                        'input[type="file"]'):
                loc = page.locator(sel).first
                try:
                    if loc.count() == 0:
                        continue
                    loc.set_input_files(resume)
                    _log(f"uploaded resume {resume}")
                    _dbg(f"resume uploaded via {sel}")
                    _sleep(2.0)
                    uploaded = True
                    break
                except Exception as exc:  # noqa: BLE001
                    _log(f"(resume upload skipped: {exc})")
                    break
    if not uploaded:
        return  # not My Experience (no upload zone) — retry on the page that has it

    _fill(page, "linkedinQuestion", ans.get("linkedin_url"))
    _fill_label(page, "LinkedIn", ans.get("linkedin_url"))

    # Citi's My Experience explicitly asks you to "List ALL employers on the application
    # regardless if they are listed on the resume or profile", so we DO fill work history
    # + education (not just upload the resume). Escape hatch: JOBAGENT_SKIP_HISTORY=1 for
    # a resume-only pass. ponytail: nth(i) assumes DOM card order == add order (true on
    # Workday); the section-ordering of the 'Add' buttons is a heuristic — see below.
    if os.environ.get("JOBAGENT_SKIP_HISTORY"):
        ans["_experience_done"] = True
        return

    # Only act on the page that actually has 'Add' buttons (My Experience) — never click
    # a stray Add elsewhere. Resume already confirmed we're on My Experience.
    if not _visible(page.get_by_role("button", name=re.compile(r"^\s*Add\s*$", re.I))):
        ans["_experience_done"] = True
        return

    # --- Work Experience ---------------------------------------------------------
    # Citi's exact ids (from a live field dump): formField-jobTitle / -companyName /
    # -location / -roleDescription, all on the input itself. Workday SAVES the draft, so
    # add ONLY enough cards to reach what we need (reuse any a prior run left), then trim
    # extras — otherwise every re-run stacks 5 more empty cards that block Submit.
    jobs = ans.get("experience", [])[:8]
    jt = "formField-jobTitle"
    guard = 0
    while _card_count(page, jt) < len(jobs) and guard < len(jobs) + 3:
        n = _card_count(page, jt)
        _click_text(page, "Add Another") if n else _click_text(page, "Add")
        try:  # wait for THIS card's fields to render before filling (not a fixed sleep)
            page.locator(f'[data-automation-id="{jt}"]').nth(n).wait_for(state="visible", timeout=8000)
        except Exception:  # noqa: BLE001
            _sleep(0.6)
        guard += 1
    _delete_extra_cards(page, jt, len(jobs))  # drop leftovers from earlier runs
    for i, job in enumerate(jobs):
        _fill_card(page, "formField-jobTitle", i, job.get("title"))
        _fill_card(page, "formField-companyName", i, job.get("company"))
        _fill_card(page, "formField-location", i, job.get("location"))
        _fill_card(page, "formField-roleDescription", i, job.get("summary"))
        (sm, sy), (em, ey) = _parse_dates(job.get("dates", ""))
        _fill_month_year(page, "startDate", sm, sy, idx=i)
        if ey:
            _fill_month_year(page, "endDate", em, ey, idx=i)
        else:
            try:  # THIS card's checkbox; real aid is formField-currentlyWorkHere (on the input)
                page.locator('[data-automation-id="formField-currentlyWorkHere"]').nth(i).check(timeout=2000)
            except Exception:  # noqa: BLE001
                pass
        _log(f"work entry {i+1}: {job.get('title')} @ {job.get('company')}")

    # --- Education ---------------------------------------------------------------
    # School / Degree / Field of Study are Workday prompts (open a popup, type into its
    # search box, pick) — handled by _pick_prompt. Same add-up-to-needed + trim logic so
    # education cards don't accumulate either. Exact ids: formField-school / -degree /
    # -fieldOfStudy / -gradeAverage / -lastYearAttended.
    edu_list = [e for e in ans.get("education_structured", []) if (e.get("school") or "").strip()] \
        or _education_guess(ans)
    guard = 0
    while _card_count(page, "formField-school") < len(edu_list) and guard < len(edu_list) + 3:
        n = _card_count(page, "formField-school")
        _click_text(page, "Add")  # work now shows 'Add Another', so first 'Add' is Education's
        try:
            page.locator('[data-automation-id="formField-school"]').nth(n).wait_for(state="visible", timeout=8000)
        except Exception:  # noqa: BLE001
            _sleep(0.6)
        guard += 1
    _delete_extra_cards(page, "formField-school", len(edu_list), preceding_aids=(jt,))
    for j, edu in enumerate(edu_list):
        _pick_prompt(page, "formField-school", edu.get("school"), idx=j)
        _pick_prompt(page, "formField-degree", edu.get("degree"), idx=j,
                     aliases=_degree_aliases(edu.get("degree")))
        _pick_prompt(page, "formField-fieldOfStudy", edu.get("field"), idx=j)
        _fill_card(page, "formField-gradeAverage", j, edu.get("gpa"))
        _fill_card(page, "formField-lastYearAttended", j, edu.get("end_year"))
        _log(f"education {j+1}: {edu.get('school')}")

    # We never fill Languages/References — delete any such card a prior run created empty
    # (its required sub-fields would otherwise block Submit).
    _delete_extra_cards(page, "formField-language", 0,
                        preceding_aids=(jt, "formField-school"))

    # --- Websites (GitHub) ---------------------------------------------------------
    gh = (ans.get("github_url") or "").strip()
    if gh:
        try:  # already on the draft/page? don't stack a duplicate card on re-runs
            have = page.evaluate(
                "() => { const i = Array.from(document.querySelectorAll('input'))"
                ".filter(i => { const r = i.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"
                ".find(i => (i.value || '').includes('github.com'));"
                " return i ? (i.id || i.name || 'input') : null; }")
        except Exception as exc:  # noqa: BLE001
            _dbg(f"websites: have-check failed ({exc})")
            have = None
        if have:
            _dbg(f"websites: github already present on '{have}' — skip")
        else:
            # A bare 'Add' also matches the Languages button above — anchor to the
            # Websites heading and take the first Add AFTER it.
            add = page.locator('xpath=(//*[normalize-space(.)="Websites"])[last()]'
                               '/following::button[normalize-space(.)="Add"][1]')
            try:
                add.first.click(timeout=2000)
                _dbg("websites: clicked Add")
                _sleep(0.8)
            except Exception:  # noqa: BLE001
                _dbg("websites: no Add button found after a 'Websites' heading")
            else:
                filled = any(_fill_card(page, aid, 0, gh) for aid in
                             ("formField-url", "formField-website", "formField-websiteUrl")) \
                    or _fill(page, "url", gh) or _fill(page, "website", gh)
                if filled:
                    _log(f"website: {gh}")
                else:  # reveal the real field ids for the next fix
                    try:
                        items = page.evaluate(
                            "() => Array.from(document.querySelectorAll('input'))"
                            ".filter(i => { const r = i.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"
                            ".map(i => i.id + '|' + (i.name || '') + '|' + "
                            "((i.closest('[data-automation-id]') || {getAttribute: () => ''}).getAttribute('data-automation-id') || ''))"
                            ".slice(-8)")
                        _dbg("websites: url field MISS; last inputs: " + " ;; ".join(items))
                    except Exception:  # noqa: BLE001
                        pass

    # LinkedIn
    _fill(page, "linkedinQuestion", ans.get("linkedin_url"))
    _fill_label(page, "LinkedIn", ans.get("linkedin_url"))
    ans["_experience_done"] = True


def _education_guess(ans: dict) -> list[dict]:
    """Pull a single rough education entry out of the free-text resume education
    block, so the school name at least lands. Degree/field/GPA are flagged for you."""
    lines = ans.get("education", [])
    if not lines:
        return []
    school = lines[0]
    gpa = None
    end_year = None
    for ln in lines:
        m = re.search(r"GPA[:\s]*([0-4]\.\d+)", ln, re.I)
        if m:
            gpa = m.group(1)
        y = re.search(r"\b(19|20)\d{2}\b", ln)
        if y:
            end_year = y.group(0)
    return [{"school": school, "gpa": gpa, "end_year": end_year}]


_DECLINE_RE = re.compile(
    r"not wish|prefer not|decline|do(n'?t| not) wish|choose not|not to (say|answer|disclose)"
    r"|not a protected veteran|not a veteran", re.I)


def _check_box(root, *selectors) -> bool:
    """Tick the first visible checkbox matching any selector (check, else click)."""
    for sel in selectors:
        locs = root.locator(sel)
        try:
            n = min(locs.count(), 8)
        except Exception:  # noqa: BLE001
            n = 0
        for i in range(n):
            loc = locs.nth(i)
            try:
                if loc.is_visible():
                    loc.check(timeout=2000)
                    return True
            except Exception:  # noqa: BLE001 - custom UI: fall back to a plain click
                try:
                    loc.click(timeout=1500)
                    return True
                except Exception:  # noqa: BLE001
                    pass
    return False


def _check_label(root, *patterns) -> bool:
    """Tick a checkbox by its visible label (e.g. EEO 'Prefer Not to Say')."""
    for p in patterns:
        try:
            cb = root.get_by_role("checkbox", name=re.compile(p, re.I)).first
            if _visible(cb):
                try:
                    cb.check(timeout=2000)
                except Exception:  # noqa: BLE001
                    cb.click(timeout=1500)
                _dbg(f"checked '{p}'")
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _decline_select(root, *control_css) -> bool:
    """Open a voluntary-disclosure dropdown and click its decline option, whatever the
    exact wording ('I do not wish to answer' / 'Prefer Not to Say' / 'I am not a
    protected veteran'). No-op (False) if the control isn't on this page."""
    ctrl = None
    for css in control_css:
        loc = root.locator(css).first
        if _visible(loc):
            ctrl = loc
            break
    if ctrl is None:
        return False
    try:
        cur = " ".join((ctrl.inner_text() or "").split())
        if cur and "select one" not in cur.lower():
            return True  # already answered
    except Exception:  # noqa: BLE001
        pass
    try:
        ctrl.click(timeout=1500)
    except Exception:  # noqa: BLE001
        return False
    _sleep(0.5)
    deadline = time.time() + 2.5 * _pace()
    while time.time() < deadline:
        for opt_sel in _OPT_SELS:
            opts = root.locator(opt_sel)
            try:
                n = min(opts.count(), 80)
            except Exception:  # noqa: BLE001
                n = 0
            for i in range(n):
                o = opts.nth(i)
                try:
                    if not o.is_visible():
                        continue
                    t = " ".join((o.inner_text() or "").split())
                except Exception:  # noqa: BLE001
                    continue
                if t and _DECLINE_RE.search(t):
                    try:
                        o.click(timeout=1500)
                        _dbg(f"decline -> {t!r}")
                        return True
                    except Exception:  # noqa: BLE001
                        pass
        _sleep(0.3)
    _dbg("decline: no matching option")
    _dump_options(root)
    try:
        _keyboard(root).press("Escape")
    except Exception:  # noqa: BLE001
        pass
    return False


def _fill_self_id(page, ans: dict) -> None:
    """Voluntary disclosures (EEO race/gender/veteran) + Terms consent + disability
    self-identify. Default preference: decline. On Citi the Terms 'I consent' checkbox
    is the one REQUIRED field; the demographic prompts are voluntary."""
    sid = ans.get("voluntary_self_id", {})
    decline = "decline"

    # --- Citi newer Workday (personalInfoUS--* / formField-* ids) ---
    # Race/ethnicity is a checkbox group; decline = 'Prefer Not to Say'.
    if sid.get("race_ethnicity", decline) == decline:
        _check_label(page, "prefer not to say", "do not wish", "decline to")
    # Gender / Hispanic-or-Latino / Veteran-status button dropdowns -> a decline option.
    _decline_select(page, '#personalInfoUS--gender', '[data-automation-id="formField-gender"]')
    _decline_select(page, '#personalInfoUS--hispanicOrLatino',
                    '[data-automation-id="formField-hispanicOrLatino"]')
    _decline_select(page, '#personalInfoUS--veteranStatus',
                    '[data-automation-id="formField-veteranStatus"]')
    # REQUIRED on Citi: accept the Terms & Conditions consent.
    _check_box(page, 'input[name="acceptTermsAndAgreements"]',
               '[data-automation-id="formField-acceptTermsAndAgreements"] input',
               '[data-automation-id="formField-acceptTermsAndAgreements"]')

    # --- Self Identify: disability self-ID form (CC-305) ---
    # A separate wizard page. Even when you decline, the form is a SIGNED document:
    # Name + today's Date are REQUIRED. disabilityStatus is a checkbox group.
    on_disability_form = (
        _any_visible(page, '[data-automation-id="disabilityStatus-CheckboxGroup"]')
        or _any_visible(page, '#selfIdentifiedDisabilityData--name'))
    if on_disability_form:
        if sid.get("disability_status", decline) == decline:
            _check_label(page, "do not want to answer", "don'?t want to answer",
                         "do not wish to answer", "don'?t wish to answer")
        fullname = f"{ans.get('first_name', '')} {ans.get('last_name', '')}".strip()
        _fill_any(page, fullname,
                  '#selfIdentifiedDisabilityData--name',
                  '[data-automation-id="formField-name"] input', 'input[name="name"]')
        _fill_any(page, time.strftime("%m"), '[data-automation-id="dateSectionMonth-input"]')
        _fill_any(page, time.strftime("%d"), '[data-automation-id="dateSectionDay-input"]')
        _fill_any(page, time.strftime("%Y"), '[data-automation-id="dateSectionYear-input"]')
        _dbg(f"disability form: signed {fullname!r} dated {time.strftime('%m/%d/%Y')}")

    # --- Legacy Workday fallbacks (other tenants) ---
    _select(page, "gender", _selfid_value(sid.get("gender", decline), "gender"))
    _select(page, "ethnicityDropdown", _selfid_value(sid.get("race_ethnicity", decline), "race"))
    _select(page, "veteranStatus", _selfid_value(sid.get("veteran_status", decline), "veteran"))
    # Disability radio on a legacy tenant's own page. Skip when we already handled Citi's
    # checkbox form above — re-clicking the same 'do not want to answer' label would
    # TOGGLE THE CHECKBOX BACK OFF and re-trigger the required-field error.
    if not on_disability_form and sid.get("disability_status", decline) == decline:
        _pick_radio_by_text(page, "don't wish to answer") or _pick_radio_by_text(page, "do not want to answer")


def _selfid_value(pref: str, kind: str) -> str:
    """Map a 'decline' preference to the phrasing Workday dropdowns actually use."""
    if pref and pref != "decline":
        return pref
    return {
        "gender": "I do not wish to answer",
        "race": "I do not wish to self-identify",
        "veteran": "I am not a veteran",
    }.get(kind, "decline")


def _clean_question(qtext: str) -> str:
    """The QUESTION alone, with Workday's own widget/validation noise stripped.

    A formField container's inner_text mixes in the button placeholder ("Select One")
    and, after a failed advance, an injected "Error: The field ... is required". Left in,
    they pollute a parked question and stop it re-matching a remembered answer next run.
    # ponytail: cut at the first known noise marker — enough for the fields Workday
    # actually renders; widen the split if the debug log shows a new marker."""
    q = " ".join((qtext or "").split())
    q = re.split(r"\bSelect One\b|\bError[:-]", q)[0]
    return q.strip().rstrip("*").strip()


def _questionnaire_answer(qtext: str, ans: dict) -> str:
    """A TRUTHFUL Yes/No for one screening question, or "" meaning 'we can't prove it —
    leave it for the human'. Priority:
      1. the human's own remembered answer (screening_answers.json, via qbank), then
      2. a fact the honesty guardrail can PROVE from the answer bank (work auth, 18+,
         sponsorship, veteran), else
      3. "" — unknown; the caller parks it for the /answers page. We NEVER guess 'No'.

    Replaces the old _compliance_answer, which blind-defaulted every unrecognized
    question to 'No' and so stated falsehoods (e.g. 'No, I don't hold a FINRA license')
    with zero human review — the exact honesty gap guardrail.py exists to close."""
    q = _clean_question(qtext)
    # Job-SPECIFIC computed answers first (salary strategy -> number, residence Yes/No
    # from location). Must precede the qbank lookup so a stored strategy word like
    # "average" becomes the computed number, not a literal typed into the box.
    smart = smartanswer.resolve(q, ans)
    if smart:
        return smart
    if smartanswer.needs_computed_salary(q):
        return ""  # salary strategy set but no number computable -> PARK; never fill "average"
    remembered = qbank.answer(q)
    if remembered:
        return remembered
    grounded = guardrail.resolve(q, ans)
    return "" if grounded == guardrail.NEEDS_HUMAN else grounded


def _open_and_pick(root, ctrl, value: str) -> bool:
    """Open a button dropdown and click the option containing `value`. Used for the
    Application-Questions Yes/No dropdowns whose ids are opaque hashes."""
    try:
        ctrl.click(timeout=1500)
    except Exception:  # noqa: BLE001
        return False
    _sleep(0.5)
    deadline = time.time() + 2.5 * _pace()
    while time.time() < deadline:
        if _match_option(root, value):
            _sleep(0.2)
            return True
        _sleep(0.3)
    _dbg(f"  no '{value}' option for question")
    _dump_options(root)
    try:
        _keyboard(root).press("Escape")
    except Exception:  # noqa: BLE001
        pass
    return False


def _fill_questionnaire(root, ans: dict) -> int:
    """Citi's 'Application Questions' pages: each question is a Yes/No button dropdown
    with an OPAQUE id (primaryQuestionnaire--<hash>) and an empty aria-label, so map by
    the QUESTION TEXT in its formField container. Citi's compliance set is all 'No'
    except 'authorized to work' (Yes). Returns how many we answered.

    Anything whose answer isn't a clean Yes/No (a free-text or multi-option question) is
    left for review rather than guessed — we only touch dropdowns we can read.

    Covers BOTH primaryQuestionnaire-- (Questions 1 of 2) and secondaryQuestionnaire--
    (Questions 2 of 2) ids; Workday also reveals follow-up questions after you answer,
    which the per-page re-run picks up on the next pass."""
    btns = root.locator('button[id*="uestionnaire--"]')
    try:
        n = min(btns.count(), 40)
    except Exception:  # noqa: BLE001
        n = 0
    answered = 0
    for i in range(n):
        btn = btns.nth(i)
        try:
            if not btn.is_visible():
                continue
            cur = " ".join((btn.inner_text() or "").split()).lower()
            if cur and "select one" not in cur:
                answered += 1  # already shows a value (e.g. on a re-run)
                continue
        except Exception:  # noqa: BLE001
            continue
        try:
            cont = btn.locator(
                'xpath=ancestor::*[starts-with(@data-automation-id,"formField-")][1]')
            qtext = " ".join((cont.inner_text() or "").split())
        except Exception:  # noqa: BLE001
            qtext = ""
        val = _questionnaire_answer(qtext, ans)
        if not val:
            # Unknown/unprovable -> park it for the human on the /answers page and leave
            # the field blank; never fabricate a 'No'. Workday's own 'required field
            # blank' then stalls the page for the human to finish. Once answered once on
            # /answers, qbank remembers it and this fills it automatically next run.
            clean = _clean_question(qtext)
            qbank.record_unknown(clean)
            failpacket.note_question(clean)  # tag it with the current screenshot for review
            _dbg(f"questionnaire PARK (needs human) <- {clean[:80]!r}")
            continue
        if _open_and_pick(root, btn, val):
            _dbg(f"questionnaire: {val} <- {qtext[:80]!r}")
            answered += 1
        else:
            _dbg(f"questionnaire MISS ({val}) <- {qtext[:80]!r}")
    return answered


def _fill_questionnaire_text(root, ans: dict) -> int:
    """Free-text questionnaire questions — the sibling of _fill_questionnaire's Yes/No
    dropdowns. Workday renders some REQUIRED questions ('Please provide reasons for
    leaving previous employers', 'salary expectations for this position') as free-text
    TEXTAREAS, which _fill_questionnaire's button-only loop never touched — so they were
    neither filled nor parked: invisible to the human on /answers AND a silent
    required-field stall that stopped the page from ever reaching Review (found live on
    the PNC Financial Advisor form, 2026-07-13). Same honesty contract: fill ONLY from
    the human's remembered answer (qbank); otherwise PARK to /answers and leave blank —
    never fabricate free text. Returns how many textareas we filled."""
    tas = root.locator('textarea[id*="uestionnaire--"]')
    try:
        n = min(tas.count(), 40)
    except Exception:  # noqa: BLE001
        n = 0
    filled = 0
    for i in range(n):
        ta = tas.nth(i)
        try:
            if not ta.is_visible():
                continue
            if (ta.input_value() or "").strip():
                filled += 1  # already has text (e.g. on a re-run) — never overwrite it
                continue
        except Exception:  # noqa: BLE001
            continue
        try:
            cont = ta.locator(
                'xpath=ancestor::*[starts-with(@data-automation-id,"formField-")][1]')
            qtext = " ".join((cont.inner_text() or "").split())
        except Exception:  # noqa: BLE001
            qtext = ""
        clean = _clean_question(qtext)
        val = _questionnaire_answer(qtext, ans)  # qbank -> guardrail(->"" for free text)
        if not val:
            qbank.record_unknown(clean)
            failpacket.note_question(clean)  # tag it with the current screenshot for review
            _dbg(f"questionnaire(text) PARK (needs human) <- {clean[:80]!r}")
            continue
        try:
            ta.fill(str(val))
            _dbg(f"questionnaire(text): filled <- {clean[:80]!r}")
            filled += 1
        except Exception:  # noqa: BLE001
            _dbg(f"questionnaire(text) MISS <- {clean[:80]!r}")
    return filled


def _strip_options_tail(q: str) -> str:
    """Drop the trailing run of radio OPTION labels ('Yes No', '... Prefer not to say')
    that a formField container's inner_text glues onto the question, so what we park
    (and later re-match) is the QUESTION alone — same job _clean_question does for the
    button-dropdown path's 'Select One' noise."""
    q = re.sub(r"(?:\s+(?:yes|no|prefer not to (?:say|answer|disclose)|decline to answer"
               r"|i don'?t wish to answer))+\s*$", "", q, flags=re.I)
    return q.strip().rstrip("*").strip()  # 'Question* Yes No' -> 'Question'


def _pick_radio_in_group(root, phrase: str, val: str) -> bool:
    """Click the `val` radio INSIDE the one formField container whose question text
    contains every word of `phrase` — never a page-global click."""
    words = [w for w in phrase.lower().split() if w]
    if not words or not val:
        return False
    conts = root.locator('[data-automation-id^="formField-"]')
    try:
        n = min(conts.count(), 40)
    except Exception:  # noqa: BLE001
        n = 0
    pat = re.compile(rf"^\s*{re.escape(val)}\s*$", re.I)
    for i in range(n):
        cont = conts.nth(i)
        try:
            if not cont.is_visible():
                continue
            text = " ".join((cont.inner_text() or "").split()).lower()
            if not all(w in text for w in words):
                continue
            opt = cont.get_by_role("radio", name=pat).first
            if opt.count():
                opt.click(timeout=1500)
                _log(f"radio [{phrase}] -> {val!r}")
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _fill_radio_screening(root, ans: dict) -> tuple[int, int]:
    """Radio-button screening questions — the OTHER Workday screening layout (some
    tenants render Yes/No screening as radio groups instead of the questionnaire's
    button dropdowns). Same honesty contract as _fill_questionnaire: the human's
    remembered answer (qbank), else a guardrail-PROVEN fact, else park the question
    on the /answers page and leave it blank. NEVER guesses.

    Each answer is clicked INSIDE its own question's formField container, so a 'No'
    can never land on a neighboring question (the old page-global exact-'No' click
    could hit ANY radio group on the page — same fabrication class as the old
    questionnaire blind-'No').

    Returns (radio_groups_seen, answered). NOT yet live-proven on a radio-layout
    tenant (PNC/Citi both use the questionnaire path); logic is unit-tested."""
    conts = root.locator('[data-automation-id^="formField-"]')
    try:
        n = min(conts.count(), 40)
    except Exception:  # noqa: BLE001
        n = 0
    seen = answered = 0
    for i in range(n):
        cont = conts.nth(i)
        try:
            if not cont.is_visible():
                continue
            radios = cont.locator('input[type="radio"]')
            if radios.count() < 2:  # a real choice has >= 2 options
                continue
            seen += 1
            if cont.locator('input[type="radio"]:checked').count():
                answered += 1  # already selected (a re-run, or Workday pre-fill)
                continue
            qtext = " ".join((cont.inner_text() or "").split())
        except Exception:  # noqa: BLE001
            continue
        q = _strip_options_tail(_clean_question(qtext))
        val = _questionnaire_answer(q, ans)
        if not val:
            # Unknown/unprovable -> park for the human on /answers, leave blank.
            qbank.record_unknown(q)
            failpacket.note_question(q)  # tag it with the current screenshot for review
            _dbg(f"screening PARK (needs human) <- {q[:80]!r}")
            continue
        pat = re.compile(rf"^\s*{re.escape(val)}\s*$", re.I)
        clicked = False
        try:
            opt = cont.get_by_role("radio", name=pat).first
            if opt.count():
                opt.click(timeout=1500)
                clicked = True
        except Exception:  # noqa: BLE001
            pass
        if not clicked:
            try:  # label text inside THIS container only
                loc = cont.get_by_text(pat).first
                if loc.count():
                    loc.click(timeout=1500)
                    clicked = True
            except Exception:  # noqa: BLE001
                pass
        if clicked:
            answered += 1
            _dbg(f"screening radio: {val} <- {q[:80]!r}")
        else:
            # We HAVE an answer but no matching option (e.g. a remembered free-text
            # answer on a Yes/No group) — leave it for the human, it's already in qbank.
            _dbg(f"screening radio MISS ({val}) <- {q[:80]!r}")
    return seen, answered


def _fill_screening(page, ans: dict) -> int:
    """Best-effort: answer each visible screening question honestly, park the rest.

    Returns the count of questions we believe we left UNANSWERED so the caller can
    warn you to scroll back. Screening varies wildly per employer; this is the one
    page most likely to need your hand."""
    # Skip pages another handler owns, so our generic exact 'Yes'/'No' text-clicks don't
    # land on the wrong control:
    #  - My Information (legalName present): its only Yes/No radio is 'previously employed
    #    here', already set by _fill_my_information.
    #  - Application Questions (Questionnaire buttons): owned by _fill_questionnaire; here
    #    a stray get_by_text('Yes') would click an answered dropdown button and reopen it.
    if _any_visible(page, '[data-automation-id="formField-legalName--firstName"]') or \
       _any_visible(page, '[data-automation-id="legalNameSection_firstName"]'):
        return 0
    try:
        if page.locator('button[id*="uestionnaire--"]').count() > 0:
            return 0
    except Exception:  # noqa: BLE001
        pass
    screen = {k.lower(): v for k, v in (ans.get("screening_answers") or {}).items()}
    # Radio-group questions, each answered/parked inside its own container.
    seen, radio_answered = _fill_radio_screening(page, ans)
    if seen == 0:
        # Legacy fallback for a layout the container walk didn't recognize — the old
        # near-universal picks. Only reached when NO radio groups were enumerable, so
        # the page-global exact-'No' click can't stomp an enumerated question.
        if ans.get("work_authorized"):
            _pick_radio_by_text(page, "authorized to work") or _select(page, "workAuthorization", "Yes")
        if ans.get("needs_sponsorship") is False:
            # "Do you require sponsorship?" -> No. exact: don't match 'November'/'Notes'.
            _pick_radio_by_text(page, "No", exact=True)
    # Desired salary — Workday wants a bare number (strip $/commas/text).
    salary = ans.get("salary_expectation")
    if salary not in (None, ""):
        sal = re.sub(r"[^\d]", "", str(salary)) or str(salary)
        (_fill_label(page, "salary", sal) or _fill_label(page, "compensation", sal)
         or _fill_label(page, "desired", sal) or _fill(page, "salary", sal))
    answered = 0
    for key, val in screen.items():
        # try a label-driven text fill, then a radio click scoped to the ONE question
        # whose text matches the key. (The old fallback clicked the first exact-value
        # radio ANYWHERE on the page — 'willing_to_relocate: Yes' could click 'Yes'
        # on a totally different question. Same fabrication class as the global 'No'.)
        label = key.replace("_", " ")
        if _fill_label(page, label, val) or _pick_radio_in_group(page, label, str(val)):
            answered += 1
    # configured answers we could NOT place + radio questions left for the human
    return (len(screen) - answered) + (seen - radio_answered)


# --------------------------------------------------------------------------- #
# Wizard driver
# --------------------------------------------------------------------------- #
def _page_sig(page) -> str:
    """A signature that changes when the wizard advances.

    Workday is a single-page app: the URL often stays `.../apply/applyManually` across
    My Information -> My Experience -> the question pages, and the heading read is
    flaky, so url+heading falsely looked 'unchanged' and we bailed thinking the page
    didn't advance. Each step exposes a DIFFERENT set of form-field automation-ids, so
    we fingerprint those (in the form frame) — a reliable 'are we still on the same
    page?' check."""
    ids = ""
    try:
        root = _form_root(page)
        ids = root.evaluate(
            "() => Array.from(document.querySelectorAll('[data-automation-id^=\"formField-\"],"
            " [data-automation-id$=\"Section\"], [data-automation-id*=\"workExperience\"],"
            " [data-automation-id*=\"fileUpload\"], [data-automation-id*=\"education\"]'))"
            ".filter(el => { const r = el.getBoundingClientRect(); return r.width>0 && r.height>0; })"
            ".map(el => el.getAttribute('data-automation-id')).sort().join(',')"
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        head = page.locator("h2").first.inner_text(timeout=800)
    except Exception:  # noqa: BLE001
        head = ""
    return f"{head.strip()}::{ids}" if ids else f"{page.url}::{head.strip()}"


def _collect_errors(root) -> list[str]:
    """Visible Workday validation messages on the current page (inline field errors +
    the top 'N errors' banner). Logged at the end of an --auto-close run so each cycle
    tells us exactly which fields Workday rejected, without watching the screen.
    De-duped, since Workday often repeats a banner. `root` is the form frame/page."""
    out: list[str] = []
    # Citi's red 'Errors Found' banner matched NONE of the exact ids (a live run logged
    # errors=[] with the banner on screen), so also take any error/alert-ish aid.
    for sel in ('[data-automation-id="errorMessage"]',
                '[data-automation-id="formMessageError"]',
                '[data-automation-id="pageHeaderErrorMessage"]',
                '[data-automation-id="errorBanner"]',
                '[data-automation-id*="rror"]',
                '[data-automation-id*="alert" i]',
                '[role="alert"]'):
        locs = root.locator(sel)
        try:
            n = min(locs.count(), 40)
        except Exception:  # noqa: BLE001
            n = 0
        for i in range(n):
            loc = locs.nth(i)
            try:
                if loc.is_visible():
                    txt = " ".join((loc.inner_text() or "").split()).strip()
                    if txt and txt not in out:
                        out.append(txt)
            except Exception:  # noqa: BLE001
                pass
    return out


def _on_review(page) -> bool:
    """The Review page shows a Submit button (and usually a 'Review' heading). The
    button is in the form iframe, so check the form root, not just the page."""
    for scope in (_form_root(page), page):
        try:
            if scope.get_by_role("button", name=re.compile(r"^\s*submit\s*$", re.I)).first.is_visible():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _next_page(page) -> bool:
    """Click Save and Continue / Next inside the form. Citi wraps the wizard in an
    IFRAME, so the nav button lives in the form frame — a page-level locator never sees
    it (that's why we 'clicked' something at page level yet the page never advanced).
    NEVER clicks a button labeled 'Submit' — a hard backstop for never-auto-submit."""
    root = _form_root(page)
    for aid in ("pageFooterNextButton", "bottom-navigation-next-button", "wizardNextButton"):
        loc = root.locator(f'[data-automation-id="{aid}"]').first
        try:
            if _visible(loc) and not re.search(r"submit", loc.inner_text(timeout=800) or "", re.I):
                loc.click(timeout=2500)
                _dbg(f"next: clicked [{aid}]")
                return True
        except Exception:  # noqa: BLE001
            pass
    if _click_text(root, "Save and Continue", "Continue", "Next", "Save"):
        _dbg("next: clicked by button text")
        return True
    _dbg("next: NO nav button found in form root")
    return False


# --------------------------------------------------------------------------- #
# Playwright tracing — opt-in via JOBAGENT_TRACE=1. Records a DOM snapshot +
# console + network for every action so a failed/stuck run can be replayed step
# by step (playwright show-trace ...). All best-effort: never changes the default
# run, never masks the real error. Shared by the Workday + generic appliers.
# --------------------------------------------------------------------------- #
def _trace_on() -> bool:
    return bool(os.environ.get("JOBAGENT_TRACE"))


def _start_trace(ctx) -> None:
    if not _trace_on():
        return
    try:
        ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
    except Exception:  # noqa: BLE001
        pass


def _stop_trace(ctx, label: str) -> None:
    if not _trace_on():
        return
    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        path = TRACE_DIR / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}-trace.zip"
        ctx.tracing.stop(path=str(path))
        print(f"\n    [trace] saved -> output/traces/{path.name}"
              f'\n            view it with:  playwright show-trace "{path}"')
    except Exception:  # noqa: BLE001
        pass


def _dump_failure(ctx, label: str) -> None:
    """On a hard crash, save what the front page looked like (PNG + HTML).
    Always fires when a failpacket is open (the unattended queue path) so a crash
    never leaves an empty folder; otherwise stays gated behind JOBAGENT_TRACE."""
    base = failpacket.attempt_dir()
    if base is None and not _trace_on():
        return
    base = base or TRACE_DIR
    try:
        pages = [pg for pg in ctx.pages if not pg.is_closed()]
        if not pages:
            return
        page = pages[-1]
        base.mkdir(parents=True, exist_ok=True)
        stamp = f"{label}-{time.strftime('%Y%m%d-%H%M%S')}-fail"
        page.screenshot(path=str(base / f"{stamp}.png"), full_page=True)
        (base / f"{stamp}.html").write_text(page.content(), encoding="utf-8")
        print(f"    [trace] failure snapshot -> {base / (stamp + '.png')}")
    except Exception:  # noqa: BLE001
        pass


def _secondary_monitor_offset():
    """Top-left (x, y) of a non-primary monitor, so the apply window parks on Ethan's
    second screen (HP 25es) instead of covering his game on the main monitor. Returns
    None on a single monitor, non-Windows, or any failure (Chrome then opens normally).
    The primary monitor sits at (0,0); the first monitor with a non-zero origin wins."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rects = []
        proc = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT), ctypes.c_double,
        )

        def _cb(_hmon, _hdc, lprc, _data):
            r = lprc.contents
            rects.append((r.left, r.top))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(0, 0, proc(_cb), 0)
        for x, y in rects:
            if (x, y) != (0, 0):  # primary is at the origin; anything else is secondary
                return x, y
    except Exception:  # noqa: BLE001
        pass
    return None


def launch_browser(p, headless: bool):
    """Launch a persistent Chromium that stays logged in to employers you've
    applied to before. Falls back to a throwaway profile if the saved one is
    locked by another running Chromium. Shared by the Workday + generic appliers."""
    PROFILE_DIR.mkdir(exist_ok=True)
    args = []
    off = _secondary_monitor_offset()
    if off:
        args.append(f"--window-position={off[0]},{off[1]}")
    try:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=headless, accept_downloads=True, args=args,
        )
    except Exception as exc:  # noqa: BLE001
        if any(k in str(exc).lower() for k in ("singletonlock", "process_singleton", "in use", "profile")):
            print("\nBrowser profile is locked (another Chromium is using it — close any "
                  "leftover 'apply' window for a still-logged-in run). Falling back to a "
                  "fresh temporary profile; you may need to sign in to this employer again.")
            ctx = p.chromium.launch_persistent_context(
                tempfile.mkdtemp(prefix="jobagent-profile-"),
                headless=headless, accept_downloads=True, args=args,
            )
        else:
            raise
    _start_trace(ctx)
    return ctx


def fill_application(url: str, wait_for_close: bool = False, auto_close: bool = False,
                     listing_id: str | None = None, job: dict | None = None) -> None:
    """Open `url`, drive the whole wizard, stop on Review for you to submit.

    auto_close: for the autonomous debug loop — after the fill loop, dump the final
    full-page screenshot + any validation errors to output/traces/, then close the
    browser and return instead of blocking. Never submits.

    listing_id: optional; when set (the queue path), a best-effort fill report is
    written so the dashboard Queue view can show 'N errors' next to the job. Purely
    observational — it can never change or break the run."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "Playwright not installed. On your Windows host run:\n"
            "    pip install playwright\n"
            "    playwright install chromium"
        )

    ans = build_answers()
    if job:
        ans["_job"] = job  # job-specific context for smartanswer (salary range, location)

    with sync_playwright() as p:
        ctx = launch_browser(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            print(f"Opening {url}")
            failpacket.start(listing_id, url, DEBUG_LOG)  # open this attempt's evidence folder
            _dbg(f"=== apply start: {url}")
            page.goto(url, wait_until="domcontentloaded")
            _sleep(2)

            if not _reach_form(page, ans.get("workday_email") or ans.get("email"),
                               ans.get("workday_password"), url=url,
                               timeout_s=_reach_timeout(auto_close)):
                _dbg("reach_form returned False — never reached the form")
                if auto_close:
                    # Unattended (queue): nobody's here to finish a login/verification,
                    # and flailing the wizard on a login page just burns more time. Fail
                    # fast — the grinder catches this, marks the job, and moves on.
                    raise RuntimeError(
                        f"couldn't reach the application form unattended within "
                        f"{_reach_timeout(auto_close)}s (login or email verification "
                        f"didn't complete) — re-add to retry when you can watch it")
                print("\nCouldn't reach the application form (still on a login/landing "
                      "page after waiting). Finish it yourself in the open window.")
            page = _front(page)

            # Walk the wizard. On each page, fire every filler (no-ops where N/A),
            # then advance — until we reach Review (Submit button) or get stuck.
            stalls = 0  # consecutive did-not-advance passes on the same page
            for step in range(14):  # ~7 real steps, but dynamic follow-up Qs re-render a page
                page = _front(page)  # follow new tabs
                _settle(page)  # wait out the page load before filling/clicking
                _sleep(0.6)  # extra paced beat: Workday paints a touch after 'settled'
                if _accept_legal_notice(page):
                    continue  # consent gate handled; re-evaluate the real page it reveals
                if _recover_if_error(page):
                    continue  # Workday "Something went wrong" -> reloaded; re-evaluate
                _wait_for_fields(page)  # Workday paints inputs a beat after 'load' — wait for them
                root = _form_root(page)  # Citi wraps the form in an iframe — target THAT, not the page
                sig = _page_sig(page)
                _dbg(f"[page {step+1}] url={page.url} sig={sig} | form root: "
                     f"{'iframe ' + (root.url or '')[:50] if root is not page else 'page'}")
                _dump_fields(page)  # per-frame field dump (real ids)
                tabs = len([pg for pg in page.context.pages if not pg.is_closed()])
                modal = _scope(page) is not page
                print(f"\n[page {step+1}] {sig.split('::',1)[-1] or '(form page)'}"
                      f"  · {tabs} tab(s){'  · MODAL OPEN' if modal else ''}")
                _shot(page, f"page-{step+1}-before")  # the blank form, as it rendered
                # Check for Review BEFORE filling: the Review page is a read-only summary,
                # and firing the radio/text fillers at it could click a summary value and
                # silently change an answer. Stop here the moment we see the Submit button.
                if _on_review(page):
                    _shot(page, "review")
                    print("\nReached the REVIEW page.")
                    break
                _fill_my_information(root, ans)
                _fill_experience(root, ans)
                _fill_questionnaire(root, ans)
                _fill_questionnaire_text(root, ans)  # free-text Qs: fill-or-park, never skip
                _fill_screening(root, ans)
                _fill_self_id(root, ans)
                _shot(page, f"page-{step+1}-after")  # what we actually did to it

                if not _next_page(page):
                    print("\nNo 'Continue/Next' button found — you're probably on a page "
                          "that needs your input. Take it from here.")
                    break
                # Workday is an SPA: after Save and Continue the field set swaps in a
                # beat or two. Poll for the signature to change rather than checking once
                # (a single early check read the still-present old page = false 'stuck').
                advanced = False
                deadline = time.time() + 15
                while time.time() < deadline:
                    _settle(page, ms=3000)
                    if _page_sig(page) != sig:
                        advanced = True
                        break
                    _sleep(0.5)
                if not advanced:
                    errs = _collect_errors(_form_root(page))
                    stalls += 1
                    _dbg(f"did-not-advance; errors={errs} (stall {stalls})")
                    if not errs and stalls < 2:
                        # No validation error — the Save-and-Continue click just didn't
                        # take (a live run stalled on a CLEAN Self Identify page). One
                        # more pass; fillers skip what's already set.
                        continue
                    print("\nPage didn't advance (likely a required field we couldn't fill, "
                          "or a validation error). Finish this page yourself.")
                    break
                stalls = 0

            print("\n" + "=" * 64)
            print("AUTOFILL DONE — nothing was submitted. Review EVERY page:")
            print("  - work history / education (Workday's parser is unreliable)")
            print("  - voluntary self-ID (set to your preference; default = decline)")
            print("  - screening questions specific to this employer")
            print("Then click Submit yourself.")
            print("=" * 64)

            if auto_close:
                # Autonomous loop: record the final state, log validation errors, leave.
                page = _front(page)
                _settle(page)
                _shot(page, "final")
                errs = _collect_errors(_form_root(page))
                if errs:
                    _dbg(f"VALIDATION ERRORS ({len(errs)}):")
                    for e in errs:
                        _dbg(f"  ! {e}")
                    print(f"\n{len(errs)} validation error(s) logged to apply-debug.log.")
                else:
                    _dbg("no validation errors detected on final page")
                    print("\nNo validation errors detected on the final page.")
                failpacket.finish("needs_human" if errs else "filled",
                                  errors=errs, step="reached Review")
                # Best-effort fill report for the dashboard. Observation only: the
                # whole block is swallowed so logging can never change the run or
                # raise. Local imports keep this off the module import path.
                try:  # noqa: BLE001 - logging must never break a real apply run
                    if listing_id:
                        from jobagent import database, fillreport
                        _conn = database.connect()
                        try:
                            # filled/flagged aren't readily observable here without
                            # touching the fill logic, so record errors + leave those
                            # empty. ponytail: errors are the actionable signal.
                            fillreport.record(_conn, listing_id, [], [], errs)
                        finally:
                            _conn.close()
                except Exception:  # noqa: BLE001 - never let logging affect the run
                    pass
            elif wait_for_close:
                print("Review and submit in the browser window — it stays open until you close it.")
                try:
                    while ctx.pages and not all(pg.is_closed() for pg in ctx.pages):
                        time.sleep(1.0)
                except Exception:  # noqa: BLE001
                    pass
            else:
                input("\nPress Enter here to close the browser when you're done... ")
        except Exception as exc:  # noqa: BLE001 - capture what Workday looked like when it broke
            _dump_failure(ctx, "workday")
            failpacket.finish("error", errors=[f"crashed: {exc}"], step="exception")
            raise
        finally:
            failpacket.finish("done")  # no-op if already finished; catches attended paths
            _stop_trace(ctx, "workday")
            try:
                ctx.close()
            except Exception:  # noqa: BLE001 - you may have already closed the window
                pass
