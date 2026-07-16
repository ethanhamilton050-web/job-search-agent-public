"""Park-and-reuse on the generic single-page filler (Greenhouse/Lever/Ashby):
leftover questions park under their REAL label text (not a cryptic field name),
and a question answered once on /answers fills itself on the next form.
"""
from jobagent import applier, qbank


class _Option:
    def __init__(self, text, value):
        self._t, self._v = text, value

    def inner_text(self):
        return self._t

    def get_attribute(self, name):
        return self._v


class _Field:
    """A visible form control: <select> or required <input>/<textarea>."""

    def __init__(self, label_text=None, name=None, value="", options=()):
        self.label_text, self.name = label_text, name
        self.value = value
        self.options = list(options)
        self.selected = None

    # -- locator-ish surface the applier touches --
    def count(self):
        return 1

    @property
    def first(self):
        return self

    def is_visible(self):
        return True

    def input_value(self):
        return self.value

    def evaluate(self, js):
        if "closest" in js:      # the react-select container check -> a plain field isn't one
            return False
        return self.label_text or ""

    def get_attribute(self, name):
        return self.name if name == "name" else None

    def fill(self, v):
        self.value = v

    def locator(self, sel):
        return _List(self.options if sel == "option" else [])

    def select_option(self, value=None):
        self.selected = value
        self.value = value


class _List:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]

    @property
    def first(self):
        return self.items[0] if self.items else _Field(name="missing")


class _Page:
    def __init__(self, selects=(), inputs=()):
        self.selects, self.inputs = list(selects), list(inputs)

    def locator(self, sel):
        if sel == "select":
            return _List(self.selects)
        if "required" in sel:
            return _List(self.inputs)
        return _List([])


def test_leftovers_park_under_their_real_label_not_the_field_name(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    q = _Field(label_text="Are you related to anyone employed at Acme? *",
               name="job_application[answers_attributes][0][text_value]")
    applier._report_unfilled(_Page(inputs=[q]))
    assert qbank.pending() == ["Are you related to anyone employed at Acme?"]


def test_unlabelable_controls_are_not_parked(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    mystery = _Field()  # no label, no name/id -> "?"
    applier._report_unfilled(_Page(inputs=[mystery]))
    assert qbank.load() == {}


def test_remembered_answer_fills_text_and_dropdown(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({"Are you related to anyone employed at Acme?": "No",
                "How did you hear about this job?": "LinkedIn"})
    text_q = _Field(label_text="Are you related to anyone employed at Acme?")
    drop_q = _Field(label_text="How did you hear about this job?",
                    options=[_Option("Company website", "web"),
                             _Option("LinkedIn", "li")])
    page = _Page(selects=[drop_q], inputs=[text_q])
    assert applier._fill_remembered(page, {}) == 2
    assert text_q.value == "No"
    assert drop_q.selected == "li"
    # nothing left to park now
    applier._report_unfilled(page)
    assert qbank.pending() == []


def test_unknown_question_is_never_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    q = _Field(label_text="Do you hold a Series 7 license?")
    page = _Page(inputs=[q])
    assert applier._fill_remembered(page, {}) == 0
    assert q.value == ""                        # untouched, no fabricated answer
    applier._report_unfilled(page)
    assert qbank.pending() == ["Do you hold a Series 7 license?"]


def test_option_matching_is_exact_or_wholeword_never_substring():
    """Audit F2: 'no' must not select 'Norway' / 'None of the above'; an exact 'No'
    option must win over a longer option that merely contains the letters."""
    # 'no' should land on the real 'No', not 'Norway' or 'None of the above'
    d = _Field(options=[_Option("Norway", "nor"),
                        _Option("None of the above", "none"),
                        _Option("No", "no")])
    assert applier._select_option_containing(d, ("no",)) is True
    assert d.selected == "no"

    # 'yes' should not fire on 'Yesterday' when there's no real Yes; whole-word only
    d2 = _Field(options=[_Option("Yesterday", "y1"), _Option("Maybe", "m")])
    assert applier._select_option_containing(d2, ("yes",)) is False
    assert d2.selected is None

    # multi-word wants still match as a whole phrase inside a longer option
    d3 = _Field(options=[_Option("United States of America", "us"),
                         _Option("United Kingdom", "uk")])
    assert applier._select_option_containing(d3, ("united states", "usa")) is True
    assert d3.selected == "us"
