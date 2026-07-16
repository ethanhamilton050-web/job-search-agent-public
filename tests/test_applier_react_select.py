"""Operating Greenhouse's react-select screening dropdowns (Carta-style: no native
<select>, an <input role=combobox> whose options render as .select__option divs).

Same honesty contract as the Workday filler: a guardrail-proven fact is selected, an
unknown parks with its real label, a multi-select preference always parks — and option
matching is exact/whole-word, never a bare substring. Verified live on Carta 2026-07-13;
these fakes guard the logic.
"""
from jobagent import applier, guardrail, qbank


class _Opt:
    def __init__(self, text):
        self.text, self.clicks = text, 0

    def inner_text(self):
        return self.text

    def click(self, timeout=None):
        self.clicks += 1


class _Keyboard:
    def press(self, key):
        pass


class _Combo:
    def __init__(self, cid, label, options, empty=True):
        self.cid, self.label = cid, label
        self.options = [_Opt(o) for o in options]
        self.empty, self.clicks = empty, 0

    def get_attribute(self, name):
        return self.cid if name == "id" else None

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def is_visible(self):
        return True

    def evaluate(self, js):
        if "placeholder" in js:
            return self.empty
        if "closest" in js:
            return False
        if "labels" in js:
            return self.label
        return ""

    def click(self, timeout=None):
        self.clicks += 1

    def chosen(self):
        return [o.text for o in self.options if o.clicks]


class _List:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]


class _Page:
    """One react-select combobox; its options surface as .select__option once opened."""

    def __init__(self, combo):
        self.combo, self.keyboard = combo, _Keyboard()

    def locator(self, sel):
        if 'role="combobox"' in sel:
            return _List([self.combo])
        if sel == ".select__option":
            return _List(self.combo.options if self.combo.clicks else [])
        return _List([])

    def wait_for_timeout(self, ms):
        pass


def test_operate_picks_exact_option_not_substring():
    combo = _Combo("question_1", "Sponsorship?", ["Norway", "No", "None of the above"])
    page = _Page(combo)
    assert applier._operate_react_select(combo, page, ("no",)) is True
    assert combo.chosen() == ["No"]          # exact 'No', never 'Norway'/'None of the above'


def test_guardrail_proven_answer_is_selected(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    combo = _Combo("question_1",
                   "Do you now or in the future require visa sponsorship to continue working?",
                   ["Yes", "No"])
    applier._fill_react_selects(_Page(combo), {"needs_sponsorship": False})
    assert combo.chosen() == ["No"]          # guardrail proved it -> operated
    assert qbank.pending() == []             # not parked


def test_unknown_question_parks_with_real_label(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    combo = _Combo("question_2", "Please confirm you can work on-site Tue-Thu.", ["Yes", "No"])
    applier._fill_react_selects(_Page(combo), {})
    assert combo.chosen() == []              # nothing proven/remembered -> not guessed
    assert qbank.pending() == ["Please confirm you can work on-site Tue-Thu."]


def test_remembered_answer_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    q = "Are you currently eligible to work in the country for any employer?"
    qbank.save({q: "Yes"})
    combo = _Combo("question_3", q, ["Yes", "No"])
    applier._fill_react_selects(_Page(combo), {})
    assert combo.chosen() == ["Yes"]         # your saved answer selected via the dropdown


def test_multiselect_preference_always_parks(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    # id ending in [] == multi-select (preferred office) — a personal choice, never auto-picked
    combo = _Combo("question_4[]", "Which is your preferred office location(s)?", ["NYC", "SF"])
    applier._fill_react_selects(_Page(combo), {"needs_sponsorship": False})
    assert combo.chosen() == []
    assert qbank.pending() == ["Which is your preferred office location(s)?"]
