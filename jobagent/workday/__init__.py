"""Workday application autofill (semi-automated, host-run).

Fills the standard repeated fields on Workday applications from a stored answer
bank, then PAUSES for you to review and submit. Never blind-mass-submits.

Runs on your Windows host (drives a real browser via Playwright), not in the
container — it needs your logins and sessions.
"""
