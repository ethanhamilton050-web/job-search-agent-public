"""Resume rendering must survive odd characters and produce a real .docx."""
from jobagent import docs


def test_docx_handles_special_chars(tmp_path):
    text = "Email <ethan@x.com>\nCut latency >50% & kept it <2ms\nUnmatched <foo bar"
    p = tmp_path / "resume.docx"
    docs.write_docx(text, p)
    assert p.exists() and p.stat().st_size > 0
