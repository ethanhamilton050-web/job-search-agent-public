"""Filler-side verification branching: code vs link, allowlist, anti-thrash dedup.

No Playwright — the page and the field helpers are stubbed so we test the decision
logic, not the browser.
"""
from jobagent.workday import filler, inbox


class FakePage:
    def __init__(self):
        self.gotos = []

    def goto(self, url, wait_until=None):
        self.gotos.append(url)


def _reset():
    filler._VERIFY_CONSUMED.clear()
    filler._VERIFY_SINCE = 0.0


def test_link_opened_once_then_deduped(monkeypatch):
    _reset()
    url = "https://pnc.wd5.myworkdayjobs.com/External/register/verifyEmail?t=1"
    monkeypatch.setattr(inbox, "fetch_workday_verification", lambda newer_than_epoch=None: {"link": url})
    page = FakePage()
    assert filler._try_email_verification(page) is True
    assert page.gotos == [url]
    # Second poll: same link is already consumed -> no navigation (no 8s thrash loop)
    assert filler._try_email_verification(page) is False
    assert page.gotos == [url]


def test_non_workday_link_is_never_opened(monkeypatch):
    _reset()
    monkeypatch.setattr(inbox, "fetch_workday_verification",
                        lambda newer_than_epoch=None: {"link": "https://evil.example.com/verifyEmail"})
    page = FakePage()
    assert filler._try_email_verification(page) is False
    assert page.gotos == []


def test_code_typed_and_submitted_then_deduped(monkeypatch):
    _reset()
    monkeypatch.setattr(inbox, "fetch_workday_verification", lambda newer_than_epoch=None: {"code": "246810"})
    typed, submitted = [], []
    monkeypatch.setattr(filler, "_fill_label", lambda page, label, val: False)
    monkeypatch.setattr(filler, "_fill", lambda page, aid, val: (typed.append((aid, val)) or True))
    monkeypatch.setattr(filler, "_check_submit", lambda page, aid: (submitted.append(aid) or True))
    page = FakePage()
    assert filler._try_email_verification(page) is True
    assert typed and typed[0][1] == "246810"
    assert submitted  # a submit/verify button was clicked
    # consumed -> a second poll won't re-type the same code
    typed.clear()
    assert filler._try_email_verification(page) is False
    assert typed == []


def test_reach_timeout_fails_fast_when_unattended():
    # Queue (auto_close=True) must use a short budget so a dead job doesn't freeze the
    # batch; the attended path keeps the long budget so you have time to log in by hand.
    assert filler._reach_timeout(auto_close=True) < filler._reach_timeout(auto_close=False)
    assert filler._reach_timeout(auto_close=False) == 300


def test_source_option_pick_is_honest_and_matches_preferred():
    pick = filler._pick_source_text
    # PNC's REAL top-level categories (from the live run screenshot)
    pnc = ["Campus", "Corporate Website", "Direct Contact", "Internet Search", "Job Board",
           "Organization", "Other", "Referral", "Social Networking"]
    # a configured value's term steers the pick (Citi value has 'website')
    assert pick(pnc, want=["Website", "Citi Jobs Career Site"]) == "Corporate Website"
    # no preferred: pick a neutral channel (website), never auto-claim a referral
    assert pick(pnc) == "Corporate Website"
    # PNC's leaf level: 'career site' is neutral
    assert pick(["PNC Career Site"]) == "PNC Career Site"
    assert "Referral" not in pick(["Referral", "Job Board"])
    # exclude stops re-clicking a category we already drilled into
    assert pick(pnc, want=["Website"], exclude={"Corporate Website"}) != "Corporate Website"
    # only a referral exists -> take it (any answer beats an empty required field)
    assert pick(["Referral"]) == "Referral"
    assert pick([]) is None


def test_degree_aliases_match_full_word_and_abbrev_lists():
    # Reproduce the matcher's substring test: any want-substring in the option text.
    def wants(value):
        al = filler._degree_aliases(value)
        return [value.lower(), " ".join(value.split()[:2]).lower()] + [a.lower() for a in al]

    def matches(value, opt):
        return any(w and w in opt.lower() for w in wants(value))

    # PNC's plural 'Bachelors' AND Citi's 'B.S.' must both match a Bachelor of Science
    assert matches("Bachelor of Science", "Bachelors")
    assert matches("Bachelor of Science", "B.S.")
    # must NOT wrongly hit a different degree level (the honesty guardrail)
    for wrong in ["Masters", "Associates", "PhD", "DBA", "No Degree", "GED", "JD", "High School Diploma"]:
        assert not matches("Bachelor of Science", wrong), wrong
    assert matches("Master of Business Administration", "Masters")
    assert matches("Associate of Arts", "Associates")


def test_unverified_account_banner_detected():
    # PNC's real "account exists but not verified" banner must trip the email-verify path
    # (the endless-sign-in-loop bug); an ordinary login page must NOT.
    assert filler._looks_unverified(
        "Verify your account before you sign in or request a verification email. "
        "Still can't sign in? ... Resend Account Verification")
    assert filler._looks_unverified("your account may need verification")
    assert not filler._looks_unverified("Sign In    Email Address    Password    Forgot your password?")
