"""setup must parse the NEWEST resume, not whichever file iterdir() yields first —
else an old resume or a cover letter can silently become the profile source-of-truth
(session-9 audit F15)."""
import os

import main


def test_pick_resume_returns_the_newest_file(tmp_path):
    old = tmp_path / "old_resume.pdf"
    new = tmp_path / "new_resume.pdf"
    old.write_text("old")
    new.write_text("new")
    # Make `old` clearly older than `new` regardless of creation order.
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    assert main._pick_resume([old, new]) == new
    assert main._pick_resume([new, old]) == new  # order-independent
