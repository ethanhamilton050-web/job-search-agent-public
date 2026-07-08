"""Pull the latest Workday verification (link or 6-digit code) from Gmail.

Workday's "Create Account" / sign-in step emails either a click-to-verify LINK
or a 6-digit CODE, always from an @*.workday.com sender (e.g. OTP.workday.com).
The filler can't proceed past that unattended, so this reads the inbox over IMAP
(stdlib only) and hands back whatever it finds.

SETUP (one time): Gmail blocks plain-password IMAP, so you need an App Password.
  1. Turn on 2-Step Verification: https://myaccount.google.com/security
  2. Create an App Password:    https://myaccount.google.com/apppasswords
You type it ONCE at a hidden prompt; it's then saved ENCRYPTED via Windows DPAPI
(tied to your Windows login — not a plaintext file), so you're never asked again.
Reset your app password? It self-heals: a rejected saved password is wiped and you
enter the new one once. Wipe it yourself with:  python -m jobagent.workday.inbox --forget

Precedence: GMAIL_APP_PASSWORD env (unattended override) > saved encrypted blob >
hidden prompt. The Gmail address comes from workday_email (workday_answers.json) or
GMAIL_USER.

Note: DPAPI protects the secret AT REST (a plain Read of the file yields ciphertext),
but code running as you can still decrypt it — there's no hiding a stored secret from
same-user code. It's meaningfully better than plaintext, not a vault.

Test it standalone:  python -m jobagent.workday.inbox
"""
from __future__ import annotations

import base64
import email
import getpass
import html
import imaplib
import os
import re
import sys
from email.utils import parsedate_to_datetime

_APP_PW_CACHE: str | None = None  # resolved once per process, held in memory
_PW_FROM_STORE = False            # did the cached password come from the saved blob?

# Only ever auto-open links on these hosts — an email link allowlist so a stray /
# phishing message in the inbox can't redirect the browser somewhere hostile.
WD_HOST_SUFFIXES = ("workday.com", "myworkday.com", "myworkdayjobs.com")
_VERIFY_HINT = re.compile(r"verif|confirm|activat|register|token|email", re.I)
_CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")  # ponytail: 6-digit OTP; widen if Workday changes length
# The activate/verify URL is NOT always a quoted href — PNC's email drops it as a raw
# URL in the body — so scan for any http(s) URL, not just href="...".
_URL = re.compile(r'https?://[^\s"\'<>)\]]+', re.I)


def _gmail_user() -> str:
    user = os.environ.get("GMAIL_USER")
    if user:
        return user.strip()
    import json  # default to the same inbox the filler signs in with
    from .. import config
    try:
        ans = json.loads((config.ROOT / "workday_answers.json").read_text("utf-8"))
        return (ans.get("workday_email") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _store_path():
    from .. import config
    return config.ROOT / ".gmail_app_password"  # git-ignored; DPAPI ciphertext, not plaintext


def _dpapi(data: bytes, protect: bool) -> bytes | None:
    """Encrypt (protect=True) or decrypt via Windows DPAPI, user scope. None on failure
    or non-Windows. ponytail: CryptProtectData/CryptUnprotectData straight over ctypes —
    no pywin32/keyring dependency for a one-secret, one-machine tool."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1,  # CRYPTPROTECT_UI_FORBIDDEN
            ctypes.byref(blob_out))
    if not ok:
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def store_password(pw: str) -> bool:
    """Save the app password encrypted (DPAPI) so it's not asked for again. False if
    it couldn't be encrypted (e.g. non-Windows) — caller just falls back to prompting."""
    enc = _dpapi(pw.encode("utf-8"), protect=True)
    if not enc:
        return False
    _store_path().write_text(base64.b64encode(enc).decode("ascii"), encoding="ascii")
    return True


def _load_stored_password() -> str:
    p = _store_path()
    if not p.exists():
        return ""
    try:
        dec = _dpapi(base64.b64decode(p.read_text("ascii")), protect=False)
        return dec.decode("utf-8") if dec else ""
    except Exception:  # noqa: BLE001  (tampered/foreign-user blob) -> treat as absent
        return ""


def forget_password() -> None:
    """Delete the saved encrypted password (run after resetting your Gmail app password)."""
    global _APP_PW_CACHE, _PW_FROM_STORE
    _APP_PW_CACHE, _PW_FROM_STORE = None, False
    try:
        _store_path().unlink()
    except FileNotFoundError:
        pass


def _app_password() -> str:
    """Resolve the Gmail app password once per process: env override, else the saved
    encrypted blob, else a hidden prompt (and save it so next time is silent). Cached
    "" means 'skipped' so we don't re-prompt on every poll."""
    global _APP_PW_CACHE, _PW_FROM_STORE
    if _APP_PW_CACHE is not None:
        return _APP_PW_CACHE

    pw = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if pw:
        _APP_PW_CACHE = pw
        return pw

    stored = _load_stored_password()
    if stored:
        _PW_FROM_STORE = True
        _APP_PW_CACHE = stored
        return stored

    try:
        pw = getpass.getpass("Gmail app password (hidden — entered once, then saved "
                             "encrypted on this PC; Enter to skip): ").replace(" ", "")
    except Exception:  # noqa: BLE001  (no tty, etc.) -> treat as skipped
        pw = ""
    if pw and store_password(pw):
        print("Saved (encrypted, this PC only) — you won't be asked again. If you ever "
              "reset it: python -m jobagent.workday.inbox --forget")
    _APP_PW_CACHE = pw
    return pw


def _host_ok(url: str) -> bool:
    m = re.match(r"https?://([^/]+)", url, re.I)
    if not m:
        return False
    host = m.group(1).split(":")[0].lower()
    return any(host == s or host.endswith("." + s) for s in WD_HOST_SUFFIXES)


def _has_path(url: str) -> bool:
    """True if the URL points somewhere real (activate/verify page) — not a bare
    homepage or logo link like http://www.workday.com, which every template carries."""
    rest = re.sub(r"^https?://[^/]+", "", url, flags=re.I)
    return len(rest.strip("/")) > 0


def _bodies(msg) -> tuple[str, str]:
    """Return (plain_text, html) decoded across all parts."""
    plain, htm = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        (plain if ctype == "text/plain" else htm).append(text)
    return "\n".join(plain), "\n".join(htm)


def parse_verification(subject: str, plain: str, htm: str) -> dict:
    """Extract {'code', 'link'} from one email's parts. Pure — unit-testable."""
    code = None
    low = f"{subject}\n{plain}".lower()
    if "code" in low or "verification" in low or "one-time" in low:  # else a stray 6-digit (phone/order #) in marketing mail becomes a bogus OTP
        m = _CODE.search(subject) or _CODE.search(plain)
        if m:
            code = m.group(1)

    # Scan the whole body for URLs (href-quoted OR raw text), drop bare homepage/logo
    # links, keep only allowlisted hosts.
    urls = (html.unescape(u).rstrip(".,);]>") for u in _URL.findall(f"{htm}\n{plain}"))
    links = [u for u in urls if _host_ok(u) and _has_path(u)]
    # Prefer the CTA (activate/verify/confirm/...) link over help/unsubscribe; fall back
    # to the first real allowlisted link. ponytail: keyword heuristic — if a future
    # template buries the real link, broaden _VERIFY_HINT.
    link = next((u for u in links if _VERIFY_HINT.search(u)), links[0] if links else None)
    return {"code": code, "link": link}


_MAX_SCAN = 12  # newest N workday emails to inspect — the OTP is always the latest


def _search_inbox(user: str, pw: str, newer_than_epoch: float | None) -> dict:
    """One IMAP session: log in, find the newest usable Workday email, parse it.
    Guarantees logout. Raises on connection/auth errors so the caller can retry."""
    M = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
    try:
        M.login(user, pw)
        M.select("INBOX")
        crit = ['FROM', 'workday.com']
        if newer_than_epoch:  # server-side date filter (day granularity) — skips the whole back-catalogue
            import time
            crit += ['SINCE', time.strftime('%d-%b-%Y', time.gmtime(newer_than_epoch - 86400))]
        typ, data = M.search(None, *crit)
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        for eid in reversed(ids[-_MAX_SCAN:]):  # highest id = most recent in Gmail
            typ, raw = M.fetch(eid, "(RFC822)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            if newer_than_epoch is not None:
                try:
                    if parsedate_to_datetime(msg["Date"]).timestamp() < newer_than_epoch:
                        break  # older than cutoff — and the rest are older still
                except Exception:  # noqa: BLE001
                    pass  # undated/odd header -> don't discard, let parsing decide
            plain, htm = _bodies(msg)
            found = parse_verification(msg.get("Subject", ""), plain, htm)
            if found.get("code") or found.get("link"):
                found["subject"] = msg.get("Subject", "")
                return found
        return {}
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass


def fetch_workday_verification(newer_than_epoch: float | None = None) -> dict:
    """Newest Workday verification email as {'code', 'link', 'subject'} (values may
    be None). Returns {} if creds are missing or nothing matches. Never raises —
    retries once on a transient IMAP hiccup, then gives up quietly."""
    user = _gmail_user()
    if not user:
        return {}
    pw = _app_password()
    if not pw:
        return {}
    last_err = None
    for attempt in range(2):  # ponytail: 1 retry covers a dropped socket; more is noise
        try:
            return _search_inbox(user, pw, newer_than_epoch)
        except imaplib.IMAP4.error as e:  # auth/protocol reject — retrying won't help
            if _PW_FROM_STORE:  # a saved password stopped working -> you reset it
                forget_password()
                print("Saved Gmail password was rejected (did you reset it?). I've cleared "
                      "it — you'll be asked for the new one next time.")
            else:
                print(f"!! Gmail login rejected: {e}")
            return {}
        except Exception as e:  # noqa: BLE001  (socket/timeout) -> retry once
            last_err = e
    print(f"!! inbox check failed: {last_err}")
    return {}


def _demo() -> None:
    sample_html = (
        '<a href="https://corp.wd5.myworkdayjobs.com/help">Help</a>'
        '<a href="https://pnc.wd5.myworkdayjobs.com/External/register/'
        'verifyEmail?token=abc123&amp;x=1">Verify Email Address</a>'
        '<a href="https://evil.example.com/verify">no</a>'
    )
    got = parse_verification("Your verification code is 482913", "code: 482913", sample_html)
    assert got["code"] == "482913", got
    assert got["link"].startswith("https://pnc.wd5.myworkdayjobs.com"), got
    assert "&" in got["link"] and "&amp;" not in got["link"], got  # entity decoded
    assert not _host_ok("https://evil.example.com/verify")
    assert _host_ok("https://pnc.wd5.myworkdayjobs.com/x")

    # The real PNC "Verify your candidate account" email: link is a RAW url (no href),
    # no code, and the only quoted link is the bare homepage. Must still find the activate url.
    raw_email = ("Verify your account: https://pnc.wd5.myworkdayjobs.com/External/activate/"
                 "xtyzd4997rqj88267/ \n<a href=\"http://www.workday.com\">Workday</a>")
    got = parse_verification("Verify your candidate account", "", raw_email)
    assert got["link"] == "https://pnc.wd5.myworkdayjobs.com/External/activate/xtyzd4997rqj88267/", got
    assert got["code"] is None, got
    assert _has_path("https://x.workday.com/activate/1") and not _has_path("http://www.workday.com")
    # a stray 6-digit in non-verification mail is NOT treated as an OTP
    assert parse_verification("Weekly digest", "order 998877 shipped", "")["code"] is None
    print("parse_verification self-check OK\n")

    user = _gmail_user()
    print(f"Gmail account: {user or '(none — set workday_email in workday_answers.json)'}")
    pw = _app_password()
    if not pw:
        print("\n> No password entered (you pressed Enter at the prompt).")
        print("> Re-run and PASTE the 16-char app password at the hidden prompt:")
        print(">   PowerShell: right-click to paste (nothing shows as you type), then Enter.")
        return
    print("Password source:", "saved (encrypted)" if _PW_FROM_STORE else "just entered + saved")

    try:  # explicit login so we can tell 'wrong password' from 'inbox empty'
        M = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
        M.login(user, pw)
        M.select("INBOX")
        typ, data = M.search(None, 'FROM', 'workday.com')
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        M.logout()
        print(f"Connected OK — {len(ids)} email(s) from workday.com in the inbox.")
    except Exception as e:  # noqa: BLE001
        print(f"\n> Login FAILED: {e}")
        if _PW_FROM_STORE:
            forget_password()
            print("> That was the SAVED password — I've cleared it. Re-run and enter the "
                  "new one (did you reset your app password?).")
        else:
            print("> Check the app password, and that it's for THIS Gmail account.")
        return

    res = fetch_workday_verification()
    if res:
        print(f"\nLatest Workday verification: {res.get('subject')!r}")
        print(f"  code: {res.get('code')}")
        print(f"  link: {res.get('link')}")
    else:
        print("\nConnected, but no recent workday.com email had a code/link.")
        print("Re-trigger a fresh PNC verification (run the apply), then try again.")


if __name__ == "__main__":
    if "--forget" in sys.argv:
        forget_password()
        print("Saved Gmail app password cleared. You'll be asked for it next run.")
    else:
        _demo()
