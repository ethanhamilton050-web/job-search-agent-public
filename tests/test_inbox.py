"""Tests for the Gmail verification reader (no network — IMAP is faked)."""
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from jobagent.workday import inbox


# --------------------------------------------------------------------------- #
# Pure parser + allowlist
# --------------------------------------------------------------------------- #
def test_parse_prefers_cta_link_and_decodes_entities():
    htm = (
        '<a href="https://corp.wd5.myworkdayjobs.com/help">Help</a>'
        '<a href="https://pnc.wd5.myworkdayjobs.com/External/register/'
        'verifyEmail?token=abc&amp;x=1">Verify Email Address</a>'
    )
    got = inbox.parse_verification("Your verification code is 482913", "code 482913", htm)
    assert got["code"] == "482913"
    assert got["link"] == "https://pnc.wd5.myworkdayjobs.com/External/register/verifyEmail?token=abc&x=1"


def test_parse_drops_non_workday_links():
    htm = '<a href="https://evil.example.com/verifyEmail?token=x">Verify</a>'
    assert inbox.parse_verification("hi", "", htm)["link"] is None


def test_parse_code_from_body_only_and_ignores_non_6_digit():
    got = inbox.parse_verification("Verify your email", "Your code: 771002 (exp 12345 / 1234567)", "")
    assert got["code"] == "771002"
    assert got["link"] is None


def test_host_allowlist():
    assert inbox._host_ok("https://x.myworkday.com/a")
    assert inbox._host_ok("https://pnc.wd5.myworkdayjobs.com/a")
    assert not inbox._host_ok("https://workday.com.evil.net/a")
    assert not inbox._host_ok("ftp://pnc.myworkdayjobs.com/a")


# --------------------------------------------------------------------------- #
# App password: env override never prompts
# --------------------------------------------------------------------------- #
def test_app_password_env_override_never_prompts(monkeypatch):
    monkeypatch.setattr(inbox, "_APP_PW_CACHE", None)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setattr(inbox.getpass, "getpass",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted!")))
    assert inbox._app_password() == "abcdefghijklmnop"  # spaces stripped, no prompt


# --------------------------------------------------------------------------- #
# Full fetch path with a fake IMAP server
# --------------------------------------------------------------------------- #
def _raw(subject, htm, plain="hello", when=None):
    m = EmailMessage()
    m["From"] = "Workday <noreply@OTP.workday.com>"
    m["Subject"] = subject
    m["Date"] = format_datetime(when or datetime.now(timezone.utc))
    m.set_content(plain)
    m.add_alternative(htm, subtype="html")
    return m.as_bytes()


class FakeIMAP:
    script: list = []       # raw email bytes; index i -> id (i+1)
    fail_times = 0          # raise on the first N constructions (transient-error sim)
    _fails = 0
    auth_fail = False       # make login() raise IMAP4.error (bad/stale password sim)
    last = None

    def __init__(self, host, timeout=None):
        if FakeIMAP._fails < FakeIMAP.fail_times:
            FakeIMAP._fails += 1
            raise OSError("transient socket drop")
        self.logged_out = False
        FakeIMAP.last = self

    def login(self, u, p):
        if FakeIMAP.auth_fail:
            raise __import__("imaplib").IMAP4.error("AUTHENTICATIONFAILED")
        return ("OK", [b""])

    def select(self, mb): return ("OK", [b"1"])

    def search(self, charset, *crit):
        ids = " ".join(str(i + 1) for i in range(len(FakeIMAP.script))).encode()
        return ("OK", [ids])

    def fetch(self, eid, spec):
        return ("OK", [(b"hdr", FakeIMAP.script[int(eid) - 1])])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b""])


def _use_fake(monkeypatch, script, fail_times=0):
    FakeIMAP.script = script
    FakeIMAP.fail_times = fail_times
    FakeIMAP._fails = 0
    FakeIMAP.auth_fail = False
    FakeIMAP.last = None
    monkeypatch.setattr(inbox, "_APP_PW_CACHE", None)
    monkeypatch.setattr(inbox, "_PW_FROM_STORE", False)
    monkeypatch.setenv("GMAIL_USER", "test@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", FakeIMAP)


def test_fetch_returns_newest_and_logs_out(monkeypatch):
    _use_fake(monkeypatch, [
        _raw("old", '<a href="https://pnc.wd5.myworkdayjobs.com/verify?t=OLD">v</a>'),
        _raw("Your code is 999888", '<a href="https://pnc.wd5.myworkdayjobs.com/verify?t=NEW">v</a>'),
    ])
    got = inbox.fetch_workday_verification()
    assert got["code"] == "999888"
    assert got["link"].endswith("t=NEW")          # newest email, not the older one
    assert FakeIMAP.last.logged_out is True        # finally-block ran


def test_fetch_cutoff_skips_stale(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    _use_fake(monkeypatch, [_raw("Your code is 111222",
              '<a href="https://pnc.wd5.myworkdayjobs.com/verify?t=x">v</a>', when=old)])
    # cutoff = 1h ago -> the 2h-old email is rejected
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    assert inbox.fetch_workday_verification(newer_than_epoch=cutoff) == {}


def test_fetch_no_match_returns_empty(monkeypatch):
    _use_fake(monkeypatch, [_raw("newsletter", '<a href="https://cnn.com">news</a>', plain="no code")])
    assert inbox.fetch_workday_verification() == {}


def test_fetch_retries_once_on_transient_error(monkeypatch):
    _use_fake(monkeypatch, [
        _raw("Your code is 424242", '<a href="https://pnc.wd5.myworkdayjobs.com/verify?t=z">v</a>'),
    ], fail_times=1)  # first connect throws, second succeeds
    got = inbox.fetch_workday_verification()
    assert got["code"] == "424242"


def test_fetch_gives_up_after_retries(monkeypatch, capsys):
    _use_fake(monkeypatch, [], fail_times=5)  # always fails
    assert inbox.fetch_workday_verification() == {}
    assert "inbox check failed" in capsys.readouterr().out


def test_fetch_no_creds_is_silent_noop(monkeypatch):
    monkeypatch.setattr(inbox, "_APP_PW_CACHE", None)
    monkeypatch.setenv("GMAIL_USER", "test@gmail.com")
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(inbox, "_load_stored_password", lambda: "")
    monkeypatch.setattr(inbox.getpass, "getpass", lambda *a, **k: "")  # user hits Enter
    assert inbox.fetch_workday_verification() == {}


# --------------------------------------------------------------------------- #
# Persistence: type once, saved encrypted, self-heals on reset
# --------------------------------------------------------------------------- #
def test_dpapi_round_trip_stores_ciphertext(tmp_path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("DPAPI is Windows-only")
    monkeypatch.setattr(inbox, "_store_path", lambda: tmp_path / ".pw")
    assert inbox.store_password("s3cr3t value!") is True
    assert b"s3cr3t" not in (tmp_path / ".pw").read_bytes()   # at-rest is ciphertext
    assert inbox._load_stored_password() == "s3cr3t value!"   # round-trips
    inbox.forget_password()
    assert not (tmp_path / ".pw").exists()


def test_app_password_prefers_saved_over_prompt(monkeypatch):
    monkeypatch.setattr(inbox, "_APP_PW_CACHE", None)
    monkeypatch.setattr(inbox, "_PW_FROM_STORE", False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(inbox, "_load_stored_password", lambda: "fromstore")
    monkeypatch.setattr(inbox.getpass, "getpass",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted!")))
    assert inbox._app_password() == "fromstore"
    assert inbox._PW_FROM_STORE is True


def test_app_password_prompt_saves_for_next_time(monkeypatch):
    monkeypatch.setattr(inbox, "_APP_PW_CACHE", None)
    monkeypatch.setattr(inbox, "_PW_FROM_STORE", False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(inbox, "_load_stored_password", lambda: "")
    monkeypatch.setattr(inbox.getpass, "getpass", lambda *a, **k: "typed pw")
    saved = {}
    monkeypatch.setattr(inbox, "store_password", lambda pw: saved.setdefault("pw", pw) or True)
    assert inbox._app_password() == "typedpw"  # spaces stripped
    assert saved["pw"] == "typedpw"            # persisted for next run


def test_fetch_self_heals_on_rejected_saved_password(monkeypatch):
    FakeIMAP.script, FakeIMAP.fail_times, FakeIMAP._fails = [], 0, 0
    FakeIMAP.auth_fail = True
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setenv("GMAIL_USER", "test@gmail.com")
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(inbox, "_APP_PW_CACHE", None)
    monkeypatch.setattr(inbox, "_PW_FROM_STORE", False)
    monkeypatch.setattr(inbox, "_load_stored_password", lambda: "stalepw")  # came from store
    forgot = {}
    monkeypatch.setattr(inbox, "forget_password", lambda: forgot.setdefault("x", True))
    assert inbox.fetch_workday_verification() == {}
    assert forgot.get("x") is True  # rejected saved password was wiped
