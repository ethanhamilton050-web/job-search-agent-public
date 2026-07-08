from jobagent import summarize as s


def test_pretty_company():
    assert s.pretty_company("sofi") == "SoFi"
    assert s.pretty_company("spgi") == "S&P Global"
    assert s.pretty_company("kkr") == "KKR"            # short vowel-less unknown -> acronym
    assert s.pretty_company("acme corp") == "Acme Corp"  # unknown -> Title Case
    assert s.pretty_company("") == ""


def test_company_blurb():
    assert "bank" in s.company_blurb("pnc").lower()
    assert s.company_blurb("nobody") == ""


def test_clean_text_repairs_mojibake():
    # U+FFFD between letters was an apostrophe; between spaces, a dash.
    assert s.clean_text("we�re") == "we're"
    assert s.clean_text("a � b") == "a — b"
    assert "�" not in s.clean_text("stray � end")


def test_format_blocks_classifies():
    text = "Who we are:\n" "Shape a brighter future.\n" "- build models\n" "* run audits"
    kinds = s.format_blocks(text)
    assert kinds[0] == ("head", "Who we are:")
    assert kinds[1][0] == "para"
    assert kinds[2] == ("bullet", "build models")
    assert kinds[3] == ("bullet", "run audits")


def test_match_breakdown():
    mb = s.match_breakdown("skills: excel, python | missing: bloomberg, sql | "
                           "keywords: valuation | location mismatch: Newark, NJ")
    assert mb["matched"] == ["excel", "python", "valuation"]
    assert mb["missing"] == ["bloomberg", "sql"]
    assert mb["notes"] == ["location mismatch: Newark, NJ"]
    empty = s.match_breakdown("")
    assert empty == {"matched": [], "missing": [], "notes": []}


def test_ai_summary_never_raises_when_box_down():
    # Unroutable port -> returns None, not an exception (dashboard must not break).
    assert s.ai_summary("desc", base="http://127.0.0.1:1", timeout=1) is None
