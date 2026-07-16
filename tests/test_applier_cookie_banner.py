"""Dismissing cookie/consent banners that cover the form (SoFi, Betterment et al.).

The one contract that must never regress: we click a REJECT/DECLINE control and NEVER
an Accept one — so the tool can't consent to non-essential cookies on Ethan's behalf.
Fakes stand in for the Playwright page; verified against real banners on the host.
"""
from jobagent import applier


class _Btn:
    def __init__(self, text, visible=True):
        self.text, self.visible, self.clicks = text, visible, 0

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return self.visible

    def click(self, timeout=None):
        self.clicks += 1


class _Empty:
    @property
    def first(self):
        return self

    def count(self):
        return 0

    def is_visible(self):
        return False

    def click(self, timeout=None):
        raise AssertionError("clicked a control that shouldn't have been visible")


class _Page:
    """role_buttons -> matched by get_by_role(name=regex); id_buttons -> by locator(css)."""

    def __init__(self, role_buttons=None, id_buttons=None):
        self.role_buttons = role_buttons or []
        self.id_buttons = id_buttons or {}

    def locator(self, sel):
        return self.id_buttons.get(sel, _Empty())

    def get_by_role(self, role, name=None):
        for b in self.role_buttons:
            if b.visible and name.search(b.text):
                return b
        return _Empty()

    def wait_for_timeout(self, ms):
        pass


def test_clicks_reject_never_accept():
    accept, reject = _Btn("Accept All"), _Btn("Reject All")
    page = _Page(role_buttons=[accept, reject])
    assert applier._dismiss_cookie_banner(page) is True
    assert reject.clicks == 1
    assert accept.clicks == 0            # the whole point: Accept is never touched


def test_reject_only_banner_is_dismissed():
    for label in ("Reject", "Decline", "Decline all", "Only necessary", "Refuse all"):
        btn = _Btn(label)
        assert applier._dismiss_cookie_banner(_Page(role_buttons=[btn])) is True, label
        assert btn.clicks == 1, label


def test_accept_only_banner_leaves_it_alone():
    # No reject option (dark-pattern banner) -> we do nothing rather than click Accept.
    accept = _Btn("Accept All Cookies")
    page = _Page(role_buttons=[accept])
    assert applier._dismiss_cookie_banner(page) is False
    assert accept.clicks == 0


def test_no_banner_is_a_noop():
    assert applier._dismiss_cookie_banner(_Page()) is False


def test_never_touches_the_application_itself():
    # Ethan's worry: could dismissing a banner accidentally click a REAL application
    # button on a company he wants, and mess up / skip the apply? It must not — the
    # dismisser only ever recognises cookie reject controls, nothing else on the page.
    app_buttons = [_Btn(t) for t in
                   ("Submit application", "Apply", "Apply for this job", "Continue",
                    "Next", "Save", "Back", "Review", "I'm interested")]
    page = _Page(role_buttons=app_buttons)
    assert applier._dismiss_cookie_banner(page) is False   # found no cookie button
    assert sum(b.clicks for b in app_buttons) == 0         # and clicked nothing at all


def test_onetrust_reject_id_is_used_first():
    reject = _Btn("Reject All")
    page = _Page(id_buttons={"#onetrust-reject-all-handler": reject})
    assert applier._dismiss_cookie_banner(page) is True
    assert reject.clicks == 1
