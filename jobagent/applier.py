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
    """Pick the first <option> whose text contains one of `wants`."""
    try:
        opts = loc.locator("option")
        for i in range(_count(opts)):
            o = opts.nth(i)
            t = (o.inner_text() or "").strip().lower()
            if t and any(w in t for w in wants):
                loc.select_option(value=o.get_attribute("value"))
                _log(f"selected {t!r}")
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
    for attr in ("aria-label", "name", "id", "placeholder"):
        try:
            v = el.get_attribute(attr)
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
    return "?"


def _report_unfilled(page) -> None:
    """List visible blank dropdowns / required-empty fields, so you know what to
    finish — and can tell me their labels to auto-handle next time."""
    left = []
    sels = page.locator("select")
    for i in range(min(_count(sels), 40)):
        s = sels.nth(i)
        try:
            if _vis(s) and (s.input_value() or "").strip().lower() in (
                    "", "select...", "select", "please select", "-", "--"):
                left.append("dropdown: " + _field_label(s))
        except Exception:  # noqa: BLE001
            pass
    reqs = page.locator('input[required], textarea[required]')
    for i in range(min(_count(reqs), 40)):
        r = reqs.nth(i)
        try:
            if _vis(r) and (r.input_value() or "").strip() == "":
                left.append("field: " + _field_label(r))
        except Exception:  # noqa: BLE001
            pass
    if left:
        print("\n  Left for you to fill (also parked in the Answers page to remember):")
        for s in left[:25]:
            print(f"    - {s}")
        # Park each leftover so it surfaces in the dashboard Answers page for a
        # one-time human answer, then it's remembered. Never blocks the run.
        from . import qbank
        for s in left:
            try:
                qbank.record_unknown(s.split(": ", 1)[-1])
            except Exception:  # noqa: BLE001 - parking is best-effort, never fatal
                pass


def _wait_until_closed(ctx) -> None:
    """Block until the user closes the browser window(s)."""
    try:
        while ctx.pages and not all(pg.is_closed() for pg in ctx.pages):
            time.sleep(1.0)
    except Exception:  # noqa: BLE001
        pass


def fill_application(url: str, wait_for_close: bool = False) -> None:
    """Open `url`, fill the one-page form, then pause for you to review + submit."""
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
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(2)

            # If no form field is visible yet, click an Apply button to reveal/open it.
            if not _vis(page.locator('input[type="email"], #first_name, [name="email"], [name="name"]')):
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

            _report_unfilled(page)

            print("\n" + "=" * 64)
            print("AUTOFILL DONE — nothing was submitted. Review the form:")
            print("  - resume-parsed fields and any custom screening questions")
            print("  - EEO / voluntary self-ID (set to decline; change if you want)")
            print("Then click Submit yourself.")
            print("=" * 64)
            if wait_for_close:
                print("Review and submit in the browser window — it stays open until you close it.")
                _wait_until_closed(ctx)
            else:
                input("\nPress Enter here to close the browser when you're done... ")
        except Exception:  # noqa: BLE001 - capture what the form looked like when it broke
            _dump_failure(ctx, "generic")
            raise
        finally:
            _stop_trace(ctx, "generic")
            try:
                ctx.close()
            except Exception:  # noqa: BLE001 - you may have already closed the window
                pass
