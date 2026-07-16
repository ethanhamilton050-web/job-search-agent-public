"""Filling SoFi-style 'Which FINRA license(s) do you hold?' checkbox lists.

Ethan holds FINRA licenses, so the tool ticks the Series boxes matching what he declared
in My Info — and touches ONLY checkboxes whose label names a Series (never an unrelated box).
Fakes stand in for the Playwright checkboxes; verified against the real SoFi form.
"""
from jobagent import applier


class _Checkbox:
    def __init__(self, label):
        self.label, self.checked = label, False

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return True

    def evaluate(self, js):
        return ""                       # no linked <label> — fall through to aria-label

    def get_attribute(self, name):
        return self.label if name == "aria-label" else None

    def check(self, timeout=None):
        self.checked = True


class _List:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]


class _Page:
    def __init__(self, boxes):
        self.boxes = boxes

    def locator(self, sel):
        return _List(self.boxes if "checkbox" in sel else [])


def test_series_num_only_matches_a_named_series():
    assert applier._series_num("Series 7 (S7)") == "7"
    assert applier._series_num("Series 63") == "63"
    assert applier._series_num("S24") == "24"
    assert applier._series_num("N/A") == ""
    assert applier._series_num("Not applicable") == ""


def test_checks_ethans_real_licenses_and_not_series_7():
    """Ethan's real credentials: Series 6, Series 63, SIE, and a Health & Life Insurance
    Producer license. Series 7 must stay UNCHECKED (he doesn't hold it), and Series 6 must
    not be confused with Series 63."""
    boxes = [_Checkbox("N/A"), _Checkbox("Series 6 (S6)"), _Checkbox("Series 7 (S7)"),
             _Checkbox("Series 63 (S63)"), _Checkbox("SIE"),
             _Checkbox("Health and Life Insurance Producer")]
    ans = {"finra_licenses": ["Series 6", "Series 63", "SIE", "Health and Life Insurance Producer"]}
    applier._fill_license_checkboxes(_Page(boxes), ans)
    assert [b.label for b in boxes if b.checked] == [
        "Series 6 (S6)", "Series 63 (S63)", "SIE", "Health and Life Insurance Producer"]
    assert not [b for b in boxes if b.label.startswith("Series 7") and b.checked]  # NOT Series 7


def test_bare_numbers_and_s_prefixes_also_match():
    boxes = [_Checkbox("Series 6 (S6)"), _Checkbox("Series 63 (S63)")]
    applier._fill_license_checkboxes(_Page(boxes), {"finra_licenses": ["Series 6", "S63"]})
    assert all(b.checked for b in boxes)


def test_no_declared_licenses_touches_nothing():
    boxes = [_Checkbox("N/A"), _Checkbox("Series 7 (S7)")]
    assert applier._fill_license_checkboxes(_Page(boxes), {}) == 0
    assert not any(b.checked for b in boxes)


def test_combined_box_is_left_for_the_human_not_falsely_claimed():
    """The back-half honesty guard. Ethan holds Series 6 & 63. A COMBINED 'Series 6/7' box
    would silently claim Series 7 if ticked, so it must be LEFT (parked) — while the clean
    'Series 63' box is still ticked."""
    boxes = [_Checkbox("Series 6/7 (combined)"), _Checkbox("Series 63 (S63)"),
             _Checkbox("Series 7 & 66")]
    ans = {"finra_licenses": ["Series 6", "Series 63"]}
    applier._fill_license_checkboxes(_Page(boxes), ans)
    assert [b.label for b in boxes if b.checked] == ["Series 63 (S63)"]  # only the clean one
    assert not boxes[0].checked   # 6/7 combined -> not falsely claimed
    assert not boxes[2].checked   # 7 & 66 -> neither held, untouched


def test_license_box_action_classifies_each_case():
    declared = {6, 63}
    lics = ["Series 6", "Series 63", "SIE", "Health and Life Insurance Producer"]
    assert applier._license_box_action("Series 6 (S6)", lics, declared) == "tick"
    assert applier._license_box_action("Series 6/7", lics, declared) == "park"   # holds 6 not 7
    assert applier._license_box_action("Series 7 (S7)", lics, declared) == "skip"  # holds neither
    assert applier._license_box_action("SIE", lics, declared) == "tick"
    assert applier._license_box_action("Health and Life Insurance Producer", lics, declared) == "tick"
    assert applier._license_box_action("Series A Preferred Stock", lics, declared) == "skip"  # not a license


def test_label_series_nums_reads_combined_and_year_safe():
    assert applier._label_series_nums("Series 6/7 (S6/S7)") == {6, 7}
    assert applier._label_series_nums("Series 7 & 63") == {7, 63}
    assert applier._label_series_nums("Series 63 (S63)") == {63}
    assert applier._label_series_nums("Series A funding 2020") == set()  # no exam number
