"""The async-wait helpers must NEVER crash a run — on any odd page state they
swallow and fall through (waiting is best-effort; acting blind is the actual bug)."""
from jobagent.workday import filler


class _BadLocator:
    """Mimics a Playwright locator whose I/O blows up (count/visible/wait_for)."""
    @property
    def first(self):
        return self

    def count(self):
        raise RuntimeError("boom")

    def is_visible(self):
        raise RuntimeError("boom")

    def nth(self, i):
        return self

    def wait_for(self, *a, **k):
        raise RuntimeError("boom")


class _BadPage:
    """Playwright-lazy: locator() is cheap; the actual waits/queries raise."""
    def wait_for_load_state(self, *a, **k):
        raise RuntimeError("boom")

    def locator(self, *a, **k):
        return _BadLocator()


def test_settle_never_raises():
    filler._settle(_BadPage(), ms=10)  # returns, does not raise


def test_wait_ready_false_on_error():
    assert filler._wait_ready(_BadPage(), "x", timeout_ms=10) is False


def test_wait_gone_or_form_returns_fast_on_error():
    filler._wait_gone_or_form(_BadPage(), timeout_s=1)  # returns, does not hang/raise
