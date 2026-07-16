"""Generic single-page ATS application filler (Greenhouse / Lever / Ashby / most).

Unlike Workday's multi-page wizard, these are one-page forms with fairly standard
fields. We fill name/contact/resume/links/salary by the usual id/name/autocomplete/
label conventions, set EEO voluntary self-ID to decline, then STOP for you to
review + submit. It NEVER auto-submits. Host-run (needs a real browser); shares the
persistent login profile with the Workday filler.

Per-employer custom questions vary; this fills what it recognizes and leaves the
rest for you — run the first one with the browser visible and watch.
"""
from __future__ import annotations

import re
import time

from . import failpacket
from .workday.answer_bank import build_answers
from .workday.filler import _dump_failure, _stop_trace, launch_browser

_DECLINE = ("decline", "don't wish", "do not wish", "prefer not", "not to answer",
            "don't want", "do not want", "not specified", "i don't wish")


def is_workday(url: str) -> bool:
    """Route Workday postings to the wizard filler; everything else here."""
    return any(k in (url or "").lower() for k in ("myworkdayjobs", "workday"))


def _log(m):
    print(f"    {m}")


def _vis(loc) -> bool:
    try:
        return bool(loc.count()) and loc.first.is_visible()
    except Exception:  # noqa: BLE001
        return False


# Cookie/consent banners cover the form on some ATS-hosted pages (SoFi, Betterment),
# so the filler can't reach the fields. We dismiss by REJECTING only — never Accept —
# so nothing non-essential is ever consented to on your behalf (privacy-safe by design).
_REJECT_BTN = re.compile(
    r"reject all|reject non|reject optional|reject cookies|decline all|decline optional|"
    r"decline cookies|only necessary|necessary only|essential only|refuse all|deny all|"
    r"^reject$|^decline$|^refuse$|^deny$", re.I)

# Known consent frameworks expose a stable reject control by id/attr — try these first.
_REJECT_IDS = (
    "#onetrust-reject-all-handler",            # OneTrust (the most common one)
    "button[aria-label*='reject all' i]",
    "button[aria-label*='decline' i]",
    ".cky-btn-reject",                         # CookieYes
    "[data-cky-tag='reject-button']",
    "#truste-consent-required",                # TrustArc (main frame only)
)


def _dismiss_cookie_banner(page) -> bool:
    """Click a consent banner's Reject/Decline button if one is covering the form.
    REJECT ONLY — never Accept — so nothing non-essential is consented to. Best-effort:
    returns True if it dismissed one, silently does nothing when there's no banner.
    ponytail: main-frame only; a banner rendered inside an <iframe> (some TrustArc)
    won't be found — add frame-walking if a real employer needs it."""
    for sel in _REJECT_IDS:
        try:
            loc = page.locator(sel).first
            if _vis(loc):
                loc.click()
                _log("dismissed cookie banner (rejected non-essential)")
                page.wait_for_timeout(400)
                return True
        except Exception:  # noqa: BLE001
            pass
    try:
        btn = page.get_by_role("button", name=_REJECT_BTN).first
        if _vis(btn):
            btn.click()
            _log("dismissed cookie banner (rejected non-essential)")
            page.wait_for_timeout(400)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


# The fields that say "this really is an application form" (not a job-description page).
# The concrete id/name controls real ATS forms use (Greenhouse #first_name, Lever/Ashby
# name=...) plus a resume upload — deliberately NOT a bare email or a fuzzy
# autocomplete="given-name", so a newsletter box or a "book a demo" widget
# (Fireblocks-style) can't masquerade as a job application.
_FORM_MARKERS = ('#first_name', 'input[name="first_name"]', 'input[name="name"]',
                 'input[type="file"]')


def _has_form_markers(root) -> bool:
    """Does this page-or-frame actually contain an application form?"""
    for m in _FORM_MARKERS:
        try:
            if root.locator(m).count():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _form_root(page):
    """Return the page-or-frame that actually holds the application form.

    Plain Greenhouse/Lever/Ashby boards keep the form in the main page. But branded
    careers sites (brex.com/careers, sofi.com/careers, betterment.com/careers) redirect
    the board link to their own page and EMBED the real Greenhouse form in an
    <iframe src="job-boards.greenhouse.io/embed/job_app">. The filler has to run INSIDE
    that frame or it just sees a description page and fills nothing. Confirmed live on
    Brex/SoFi/Betterment 2026-07-14."""
    if _has_form_markers(page):
        return page
    for f in page.frames:                       # prefer the Greenhouse embed frame
        try:
            if "greenhouse.io/embed" in (f.url or "") and _has_form_markers(f):
                return f
        except Exception:  # noqa: BLE001
            pass
    for f in page.frames:                       # else any other frame holding the form
        try:
            if f is not page.main_frame and _has_form_markers(f):
                return f
        except Exception:  # noqa: BLE001
            pass
    return page


def _fill_css(page, selectors, value) -> bool:
    """Fill the first visible input matched by any of the css `selectors`."""
    if value in (None, ""):
        return False
    for sel in selectors:
        loc = page.locator(sel).first
        if _vis(loc):
            try:
                loc.fill(str(value))
                _log(f"set {sel} = {value!r}")
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _fill_label(page, label, value) -> bool:
    if value in (None, ""):
        return False
    try:
        loc = page.get_by_label(re.compile(label, re.I)).first
        if _vis(loc):
            loc.fill(str(value))
            _log(f"set [{label}] = {value!r}")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _count(loc) -> int:
    try:
        return loc.count()
    except Exception:  # noqa: BLE001
        return 0


def _select_option_containing(loc, wants) -> bool:
    """Pick the <option> that best matches one of `wants`: an EXACT text match wins,
    else a WHOLE-WORD match. Never a bare substring — "no" must not select "Norway"
    or "None of the above", the crude-substring honesty bug from the session-9 audit
    (F2). Exact-first also means a real "No" option beats "None of the above"."""
    wants = [w.strip().lower() for w in wants if w and str(w).strip()]
    if not wants:
        return False
    try:
        opts = loc.locator("option")
        exact = word = None
        for i in range(_count(opts)):
            o = opts.nth(i)
            tl = (o.inner_text() or "").strip().lower()
            if not tl:
                continue
            if tl in wants:
                exact = o
                break  # can't do better than an exact match
            if word is None and any(re.search(rf"\b{re.escape(w)}\b", tl) for w in wants):
                word = o
        pick = exact or word
        if pick is not None:
            loc.select_option(value=pick.get_attribute("value"))
            _log(f"selected {(pick.inner_text() or '').strip()!r}")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _decline_select(loc) -> bool:
    return _select_option_containing(loc, _DECLINE)


def _answer_label(page, label_re, wants) -> bool:
    """Find a <select> by its label text and pick an option containing one of `wants`."""
    try:
        loc = page.get_by_label(re.compile(label_re, re.I)).first
        if _vis(loc):
            return _select_option_containing(loc, wants)
    except Exception:  # noqa: BLE001
        pass
    return False


def _field_label(el) -> str:
    """The human-readable QUESTION for a control — its real <label> first (what the
    applicant sees, e.g. 'Have you worked at Acme before?'), then aria-label/
    placeholder, then the old cryptic name/id fallback. Parking the real question
    (not 'job_application[answers_attributes][0][text_value]') is what lets the
    /answers page show something a human can actually answer — and re-match it on
    the next employer that asks the same thing."""
    try:
        t = el.evaluate("el => el.labels && el.labels.length ? el.labels[0].textContent : ''")
        t = " ".join((t or "").split()).strip().rstrip("*").strip()
        if t:
            return t
    except Exception:  # noqa: BLE001
        pass
    for attr in ("aria-label", "placeholder"):
        try:
            v = el.get_attribute(attr)
            if v and v.strip():
                return v.strip()
        except Exception:  # noqa: BLE001
            pass
    # Newer Greenhouse/Lever React forms use native <select>s with NO linked <label>
    # and no id/name (found live on Carta 2026-07-13: 4 real questions parked as "?").
    # Fall back to the nearest label text in the field's own container.
    # ponytail: nearest-label walk, ≤4 ancestors; tight enough for these field wrappers,
    # widen if a form nests questions deeper.
    try:
        t = el.evaluate("""el => {
            let p = el.previousElementSibling;
            while (p) { if (p.tagName==='LABEL' && p.textContent.trim()) return p.textContent; p = p.previousElementSibling; }
            let n = el.parentElement, d = 0;
            while (n && d < 4) {
                const lab = n.querySelector('label, legend');
                if (lab && lab.textContent.trim()) return lab.textContent;
                n = n.parentElement; d++;
            }
            return '';
        }""")
        t = " ".join((t or "").split()).strip().rstrip("*").strip()
        if t:
            return t
    except Exception:  # noqa: BLE001
        pass
    for attr in ("name", "id"):
        try:
            v = el.get_attribute(attr)
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
    return "?"


def _react_select_questions(page) -> list:
    """Greenhouse's custom react-select screening dropdowns (Carta et al. render these
    instead of a native <select>): an <input role=combobox id=question_*> inside a
    .select__container. Returns [(label, combo_locator, is_multi)] for the EMPTY ones.
    Skips the Country field + the phone country-code widget (id doesn't start question_)."""
    out = []
    combos = page.locator('input[role="combobox"]')
    for i in range(min(_count(combos), 30)):
        c = combos.nth(i)
        try:
            cid = c.get_attribute("id") or ""
            if not cid.startswith("question_") or not _vis(c):
                continue
            # placeholder still present == nothing chosen yet
            empty = c.evaluate(
                "el => !!el.closest('.select__container')"
                "  && !!el.closest('.select__container').querySelector('.select__placeholder')")
            if not empty:
                continue
            out.append((_field_label(c), c, cid.endswith("[]")))
        except Exception:  # noqa: BLE001
            continue
    return out


def _operate_react_select(combo, page, wants) -> bool:
    """Open a react-select and click the option matching one of `wants` (exact, else
    whole-word — same anti-substring rule as the native path). Scopes to .select__option
    so the phone widget's 200+ country list can never be clicked by mistake."""
    wants = [w.strip().lower() for w in wants if w and str(w).strip()]
    if not wants:
        return False
    try:
        combo.click()
        page.wait_for_timeout(500)
        opts = page.locator(".select__option")
        exact = word = None
        for i in range(min(_count(opts), 40)):
            tl = (opts.nth(i).inner_text() or "").strip().lower()
            if not tl:
                continue
            if tl in wants:
                exact = opts.nth(i)
                break
            if word is None and any(re.search(rf"\b{re.escape(w)}\b", tl) for w in wants):
                word = opts.nth(i)
        pick = exact or word
        if pick is not None:
            txt = (pick.inner_text() or "").strip()
            pick.click()
            _log(f"react-select chose {txt!r}")
            page.wait_for_timeout(200)
            return True
        try:
            combo.press("Escape")  # nothing matched — close the menu, leave it blank
        except Exception:  # noqa: BLE001 - .keyboard isn't on a Frame; press via the element
            pass
    except Exception:  # noqa: BLE001
        pass
    return False


def _grounded_answer(label, ans) -> str:
    """The HONEST answer for a screening question, or "" to park it. Order: your remembered
    answer (qbank) -> a fact the guardrail can PROVE -- a universal Yes/No, a VALUE you gave
    (highest education, home country), "ever worked at X" / "currently a <co> employee" (from
    your full job history), or a related-party "No" (you affirmed you have none). Never
    guesses: an unproven question returns "" and gets parked for /answers."""
    from . import guardrail, qbank
    a = qbank.answer(label)
    if a:
        return a
    for r in (guardrail.resolve(label, ans),
              guardrail.value_answer(label, ans),
              guardrail.ever_worked_answer(label, ans),
              guardrail.current_employer_answer(label, ans),
              guardrail.related_party_answer(label, ans),
              guardrail.disclosure_answer(label, ans),
              guardrail.finra_answer(label, ans)):
        if r and r != guardrail.NEEDS_HUMAN:
            return r
    return ""


def _series_num(text) -> str:
    """The Series number named in a license label ('Series 7 (S7)' -> '7'), or '' if none.
    Only matches when a Series is actually named, so it can't fire on a stray number."""
    m = re.search(r"series\s*(\d+)|(?<![a-z0-9])s(\d+)\b", (text or "").lower())
    return (m.group(1) or m.group(2)) if m else ""


def _label_series_nums(label: str) -> set:
    """EVERY FINRA Series number a checkbox label names — {6, 7} for a COMBINED 'Series 6/7',
    {63} for 'Series 63 (S63)'. Catches the list/combined forms ('6/7', '7 & 63', '6, 66')
    that a single-number read would miss — those combined boxes are the whole reason a wrong
    license got claimed: ticking 'Series 6/7' for a Series-6 holder silently claims Series 7."""
    lab = (label or "").lower()
    nums: set = set()
    for m in re.finditer(r"series\s*(\d{1,3}(?:\s*(?:,|&|/|and|-)\s*\d{1,3})*)", lab):
        nums |= {int(n) for n in re.findall(r"\d{1,3}", m.group(1))}
    nums |= {int(m.group(1)) for m in re.finditer(r"(?<![a-z0-9])s(\d{1,3})\b", lab)}
    return nums


def _declared_series_nums(lics) -> set:
    """The Series numbers YOU declared (from 'Series 6' / 'S63' style entries)."""
    nums: set = set()
    for lic in lics:
        s = str(lic).lower()
        if re.search(r"series|(?<![a-z0-9])s\d", s):
            m = re.search(r"\d{1,3}", s)
            if m:
                nums.add(int(m.group(0)))
    return nums


def _license_box_action(label, lics, declared_series) -> str:
    """How to treat one license checkbox under the honesty contract — the picker's 'prove it
    or ask you' rule, the same one that guards your Yes/No answers:

      'tick' — EVERY credential the box names is one you declared, and it names at least one
               you hold. Ticking it makes no false claim.
      'park' — the box names a credential you hold AND one you DON'T (a combined 'Series 6/7'
               box when you hold only 6). Never auto-claim it — leave it and flag it for you.
      'skip' — not one of your licenses (or not a license box). Leave it untouched.
    """
    lab = (label or "").lower()
    box_nums = _label_series_nums(label)
    if box_nums:                                    # a FINRA Series box
        overlap = box_nums & declared_series
        if not overlap:
            return "skip"                           # none of your Series -> not your box
        return "tick" if box_nums <= declared_series else "park"  # extra number = false claim
    if re.search(r"\bsie\b|securities industry essentials", lab):
        held = any(re.search(r"\bsie\b|securities industry essentials", str(l).lower()) for l in lics)
        return "tick" if held else "skip"
    if "insurance" in lab and any(w in lab for w in ("life", "health", "producer", "accident")):
        return "tick" if any("insurance" in str(l).lower() for l in lics) else "skip"
    return "skip"


def _fill_license_checkboxes(root, ans) -> int:
    """Tick the license checkboxes for what you declared in My Info (Series exams, SIE,
    insurance producer) — but ONLY a box that makes no false claim. A box that also names a
    license you didn't declare (a combined 'Series 6/7') is left unticked and flagged for you,
    never auto-claimed. Does nothing if you declared no licenses (the question parks for you).
    Returns the number of boxes ticked."""
    lics = [str(x).strip() for x in (ans.get("finra_licenses") or []) if str(x).strip()]
    if not lics:
        return 0
    declared_series = _declared_series_nums(lics)
    boxes = root.locator('input[type="checkbox"]')
    filled = 0
    for i in range(min(_count(boxes), 80)):
        cb = boxes.nth(i)
        try:
            if not _vis(cb):
                continue
            label = _field_label(cb)
            action = _license_box_action(label, lics, declared_series)
            if action == "tick":
                cb.check()
                _log(f"checked license {label!r}")
                filled += 1
            elif action == "park":
                _log(f"LEFT FOR YOU: license box {label!r} names a credential you didn't "
                     f"declare (holds one, not the other) — verify it on the review page")
        except Exception:  # noqa: BLE001
            pass
    return filled


def _fill_react_selects(page, ans) -> None:
    """Answer react-select screening dropdowns HONESTLY via `_grounded_answer` (qbank -> a
    guardrail-proven fact -> else park the real question for /answers). Never guesses; a
    multi-select (a personal preference like preferred office) always parks."""
    from . import qbank
    parked = []
    for label, combo, is_multi in _react_select_questions(page):
        val = "" if is_multi else _grounded_answer(label, ans)
        if val and _operate_react_select(combo, page, (val.lower(),)):
            continue
        if label and label != "?":
            try:
                qbank.record_unknown(label)  # park with the real question -> answer once
                parked.append(label)
            except Exception:  # noqa: BLE001
                pass
    if parked:
        print("\n  Dropdowns left for you (parked in Answers to remember):")
        for label in parked[:25]:
            print(f"    - {label}")


def _empty_fields(page) -> list:
    """Visible blank dropdowns / required-empty fields -> [(kind, label, locator)].
    React-select comboboxes are owned by _fill_react_selects, so they're skipped here."""
    out = []
    sels = page.locator("select")
    for i in range(min(_count(sels), 40)):
        s = sels.nth(i)
        try:
            if _vis(s) and (s.input_value() or "").strip().lower() in (
                    "", "select...", "select", "please select", "-", "--"):
                out.append(("dropdown", _field_label(s), s))
        except Exception:  # noqa: BLE001
            pass
    reqs = page.locator('input[required], textarea[required]')
    for i in range(min(_count(reqs), 40)):
        r = reqs.nth(i)
        try:
            if not _vis(r) or (r.input_value() or "").strip() != "":
                continue
            try:
                in_react_select = bool(
                    r.evaluate("el => !!el.closest('.select__container,.select-shell')"))
            except Exception:  # noqa: BLE001 - not a real browser el (tests) -> treat as plain
                in_react_select = False
            if in_react_select:
                continue  # react-select's internal required input — _fill_react_selects owns it
            out.append(("field", _field_label(r), r))
        except Exception:  # noqa: BLE001
            pass
    return out


def _fill_remembered(page, ans) -> int:
    """Fill leftover native dropdowns / text fields from a HONEST answer (`_grounded_answer`:
    your remembered /answers via qbank, or a guardrail-proven fact — education, home country,
    'ever worked at X', related-party 'No'). Only a proven/remembered answer is ever used;
    nothing is guessed. This is the reuse half of park-and-reuse for the native fields."""
    filled = 0
    for kind, label, loc in _empty_fields(page):
        a = _grounded_answer(label, ans)
        if not a:
            continue
        try:
            if kind == "dropdown":
                ok = _select_option_containing(loc, (a.lower(),))
            else:
                loc.fill(a)
                ok = True
            if ok:
                _log(f"grounded [{label}] = {a!r}")
                filled += 1
        except Exception:  # noqa: BLE001
            pass
    return filled


def _report_unfilled(page) -> None:
    """List visible blank dropdowns / required-empty fields, so you know what to
    finish — and park each on the dashboard Answers page so answering it ONCE
    makes it fill automatically from then on. Never blocks the run."""
    left = [(kind, label) for kind, label, _ in _empty_fields(page)]
    if left:
        print("\n  Left for you to fill (also parked in the Answers page to remember):")
        for kind, label in left[:25]:
            print(f"    - {kind}: {label}")
        from . import qbank
        for _, label in left:
            if label == "?":
                continue  # unlabelable control — nothing a human could re-match
            try:
                qbank.record_unknown(label)
            except Exception:  # noqa: BLE001 - parking is best-effort, never fatal
                pass


def _wait_until_closed(ctx) -> None:
    """Block until the user closes the browser window(s)."""
    try:
        while ctx.pages and not all(pg.is_closed() for pg in ctx.pages):
            time.sleep(1.0)
    except Exception:  # noqa: BLE001
        pass


def fill_application(url: str, wait_for_close: bool = False,
                     auto_close: bool = False) -> None:
    """Open `url`, fill the one-page form, then pause for you to review + submit.

    auto_close: autonomous debug mode (mirrors the Workday filler) — snapshot the filled
    form to output/traces/ and close, instead of blocking on a keypress. Never submits."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("Playwright not installed. On your Windows host run:\n"
                         "    pip install playwright\n    playwright install chromium")

    ans = build_answers()
    first, last = ans.get("first_name", ""), ans.get("last_name", "")
    full = f"{first} {last}".strip()

    with sync_playwright() as p:
        ctx = launch_browser(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            print(f"Opening {url}")
            failpacket.start(url=url)  # open this attempt's evidence folder
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(2)
            _dismiss_cookie_banner(page)  # a consent banner can cover the whole form

            # Find the form. Plain boards keep it in the page; branded careers sites
            # (brex/sofi/betterment) embed it in an iframe — _form_root finds either.
            root = _form_root(page)
            # Nothing yet? Click an Apply button to reveal/open it, then look again.
            if root is page and not _has_form_markers(page):
                for t in ("Apply for this job", "Apply Now", "Apply", "I'm interested"):
                    try:
                        for role in ("button", "link"):
                            b = page.get_by_role(role, name=re.compile(t, re.I)).first
                            if _vis(b):
                                b.click()
                                time.sleep(2)
                                break
                        else:
                            continue
                        break
                    except Exception:  # noqa: BLE001
                        pass
                root = _form_root(page)

            # Still no form anywhere -> this link is a job description / redirect, not an
            # application (Fireblocks-style). Say so plainly instead of "filling" nothing.
            if root is page and not _has_form_markers(page):
                print("\n" + "=" * 64)
                print("NO APPLICATION FORM on this page.")
                print("This link opens a job description or redirects to the company's own")
                print("site — there's no form here to fill. Open it and apply yourself:")
                print(f"  {url}")
                print("=" * 64)
                failpacket.finish("no_form",
                                  errors=["no application form found on page (description/redirect)"],
                                  step="no-form")
                if auto_close:
                    _dump_failure(ctx, "generic-noform")
                elif wait_for_close:
                    _wait_until_closed(ctx)
                else:
                    input("\nPress Enter here to close the browser... ")
                return

            if root is not page:
                _log("form is in an embedded frame — filling inside it")
                for _ in range(12):          # let the embedded form finish rendering
                    if _vis(root.locator(", ".join(_FORM_MARKERS)).first):
                        break
                    page.wait_for_timeout(500)
            page = root                      # fill against the form's page-or-frame

            print("Filling application fields...")
            # Name — Greenhouse uses first/last; Lever uses a single full-name field.
            _fill_css(page, ('#first_name', 'input[name="first_name"]',
                             'input[autocomplete="given-name"]'), first) \
                or _fill_label(page, "first name", first)
            _fill_css(page, ('#last_name', 'input[name="last_name"]',
                             'input[autocomplete="family-name"]'), last) \
                or _fill_label(page, "last name", last)
            _fill_css(page, ('input[name="name"]', 'input[autocomplete="name"]'), full) \
                or _fill_label(page, "full name", full)
            _fill_css(page, ('#email', 'input[type="email"]', 'input[name="email"]',
                             'input[autocomplete="email"]'), ans.get("email")) \
                or _fill_label(page, "email", ans.get("email"))
            _fill_css(page, ('#phone', 'input[type="tel"]', 'input[name="phone"]',
                             'input[autocomplete="tel"]'), ans.get("phone")) \
                or _fill_label(page, "phone", ans.get("phone"))
            # Preferred name -> just your real first name.
            _fill_css(page, ('input[name*="preferred" i]',), first) \
                or _fill_label(page, "preferred name", first)
            # Country -> United States (dropdown or text).
            country = ans.get("country", "United States")
            _answer_label(page, "country", ("united states", "usa", "u.s")) \
                or _fill_css(page, ('input[name*="country" i]', 'input[autocomplete="country-name"]'), country) \
                or _fill_label(page, "country", country)
            # City / location (from your workday_answers.json address, if set).
            city = ans.get("city")
            if city:
                _fill_css(page, ('input[name*="city" i]', 'input[autocomplete="address-level2"]'), city) \
                    or _fill_label(page, "city", city)
                _fill_label(page, "location", city)

            # Resume upload (first file input; works on the hidden Greenhouse/Lever inputs).
            resume = ans.get("resume_file", "")
            if resume:
                fi = page.locator('input[type="file"]').first
                try:
                    if fi.count():
                        fi.set_input_files(resume)
                        _log(f"uploaded resume {resume}")
                        time.sleep(1.5)
                except Exception as exc:  # noqa: BLE001
                    _log(f"(resume upload skipped: {exc})")

            # Links
            _fill_css(page, ('input[name="urls[LinkedIn]"]', 'input[name*="linkedin" i]'),
                      ans.get("linkedin_url")) or _fill_label(page, "linkedin", ans.get("linkedin_url"))
            _fill_css(page, ('input[name*="github" i]',), ans.get("github_url"))

            # Desired salary — strip to a bare number if a salary field exists.
            salary = ans.get("salary_expectation")
            if salary not in (None, ""):
                sal = re.sub(r"[^\d]", "", str(salary)) or str(salary)
                _fill_label(page, "salary", sal) or _fill_label(page, "compensation", sal)

            # Best-effort screening answers by their label text.
            for key, val in (ans.get("screening_answers") or {}).items():
                _fill_label(page, key.replace("_", " "), val)

            # Common dropdown questions (new Greenhouse renders these as native <select>).
            if ans.get("work_authorized"):
                _answer_label(page, "authoriz", ("yes",))
            if ans.get("needs_sponsorship") is False:
                _answer_label(page, "sponsor", ("no",)) or _answer_label(page, "visa", ("no",))
            _answer_label(page, "hear about", ("website", "linkedin", "company", "indeed"))

            # EEO / voluntary self-ID -> decline (by select id/name AND by label).
            for kw in ("gender", "race", "ethnic", "veteran", "disability", "hispanic", "latino"):
                for sel in (f'select[id*="{kw}" i]', f'select[name*="{kw}" i]',
                            f'select[aria-label*="{kw}" i]'):
                    loc = page.locator(sel).first
                    if _vis(loc):
                        _decline_select(loc)
                _answer_label(page, kw, _DECLINE)

            _fill_react_selects(page, ans)  # Greenhouse react-select dropdowns (Carta-style)
            _fill_license_checkboxes(page, ans)  # "which FINRA licenses?" checkbox list
            _fill_remembered(page, ans)  # saved /answers + grounded facts fill themselves
            _report_unfilled(page)   # whatever's left is parked there for you

            print("\n" + "=" * 64)
            print("AUTOFILL DONE — nothing was submitted. Review the form:")
            print("  - resume-parsed fields and any custom screening questions")
            print("  - EEO / voluntary self-ID (set to decline; change if you want)")
            print("Then click Submit yourself.")
            print("=" * 64)
            if wait_for_close:
                print("Review and submit in the browser window — it stays open until you close it.")
                _wait_until_closed(ctx)
            elif auto_close:
                _dump_failure(ctx, "generic-final")  # snapshot the filled form for review
                print("\nauto-close: form filled, screenshots saved to output/traces/, "
                      "closing (nothing submitted).")
            else:
                input("\nPress Enter here to close the browser when you're done... ")
        except Exception as exc:  # noqa: BLE001 - capture what the form looked like when it broke
            _dump_failure(ctx, "generic")
            failpacket.finish("error", errors=[f"crashed: {exc}"], step="exception")
            raise
        finally:
            failpacket.finish("done")  # no-op if already finished
            _stop_trace(ctx, "generic")
            try:
                ctx.close()
            except Exception:  # noqa: BLE001 - you may have already closed the window
                pass
