"""Finding the application form when it's embedded in an <iframe>.

Branded careers sites (brex.com/careers, sofi.com/careers, betterment.com/careers)
redirect the Greenhouse board link to their own page and embed the REAL form in a
job-boards.greenhouse.io/embed/job_app iframe. `_form_root` must return that frame so
the filler works inside it; a page that's just a job description / marketing page (a
lone newsletter email, a "book a demo" widget — Fireblocks-style) must NOT look like a
form. Verified live on all four employers 2026-07-14; these fakes guard the logic.
"""
from jobagent import applier


class _Loc:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class _Root:
    """A page or frame that 'contains' a fixed set of css selectors."""

    def __init__(self, url, present):
        self.url = url
        self.present = set(present)

    def locator(self, sel):
        return _Loc(1 if sel in self.present else 0)


class _FakePage:
    def __init__(self, main, others=()):
        self._main = main
        self.frames = [main, *others]

    def locator(self, sel):
        return self._main.locator(sel)

    @property
    def main_frame(self):
        return self._main


def _flagged_no_form(page):
    """Mirror fill_application's decision: no reachable form anywhere."""
    root = applier._form_root(page)
    return root is page and not applier._has_form_markers(page)


def test_plain_board_form_stays_on_the_page():
    main = _Root("https://boards.greenhouse.io/acme/jobs/1", ["#first_name", 'input[type="file"]'])
    page = _FakePage(main)
    assert applier._form_root(page) is page            # the page itself
    assert not _flagged_no_form(page)


def test_embedded_greenhouse_form_is_found_in_the_iframe():
    main = _Root("https://www.brex.com/careers/123", [])          # branded page, no form
    embed = _Root("https://job-boards.greenhouse.io/embed/job_app?for=brex",
                  ["#first_name", 'input[type="file"]'])
    page = _FakePage(main, others=[embed])
    assert applier._form_root(page) is embed           # reach into the frame
    assert not _flagged_no_form(page)


def test_newsletter_email_is_not_mistaken_for_a_form():
    # Fireblocks-style: a marketing page whose only field is a newsletter email box.
    main = _Root("https://www.fireblocks.com/careers/9", ['input[type="email"]'])
    page = _FakePage(main)
    assert applier._form_root(page) is page
    assert _flagged_no_form(page)                       # -> "open it yourself", not a fake fill


def test_book_a_demo_given_name_is_not_a_form():
    # a fuzzy autocomplete=given-name (contact/demo widget) must not count as an application
    main = _Root("https://www.fireblocks.com/careers/9", ['input[autocomplete="given-name"]'])
    page = _FakePage(main)
    assert _flagged_no_form(page)


def test_pure_description_page_flags_no_form():
    main = _Root("https://www.company.com/jobs/42", [])           # nothing form-like at all
    page = _FakePage(main)
    assert _flagged_no_form(page)
