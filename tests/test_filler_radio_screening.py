"""The radio-button screening path (_fill_radio_screening) keeps the same honesty
contract as the questionnaire path: remembered answer -> proven fact -> PARK, never
a guess — and every click stays INSIDE its own question's container.

Fake locators mirror exactly the Playwright surface the code touches (same style as
_FakeRoot in test_filler_parse.py); real-browser behavior still needs one live run
on a radio-layout tenant, which is noted in ISSUES.md.
"""
import re

from jobagent import qbank
from jobagent.workday import filler

FACTS = {"work_authorized": True, "needs_sponsorship": False,
         "is_over_18": True, "is_veteran": False}


class _Radio:
    def __init__(self, label, checked=False):
        self.label, self.checked, self.clicks = label, checked, 0

    def count(self):
        return 1

    def click(self, timeout=None):
        self.clicks += 1


class _Empty:
    def count(self):
        return 0


class _List:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]

    @property
    def first(self):
        return self.items[0] if self.items else _Empty()


class _Group:
    """A formField container holding one radio-group question."""

    def __init__(self, question, options=("Yes", "No"), checked=None, visible=True):
        self.question = question
        self.options = [_Radio(o, checked=(o == checked)) for o in options]
        self.visible = visible

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return f"{self.question}* " + " ".join(o.label for o in self.options)

    def locator(self, sel):
        if ":checked" in sel:
            return _List([o for o in self.options if o.checked])
        if 'input[type="radio"]' in sel:
            return _List(self.options)
        return _List([])

    def get_by_role(self, role, name=None):
        if role != "radio":
            return _List([])
        return _List([o for o in self.options if name and name.match(o.label)])

    def get_by_text(self, pat):
        return _List([o for o in self.options if pat.search(o.label)])

    def clicked(self):
        return [o.label for o in self.options if o.clicks]


class _Root:
    def __init__(self, groups):
        self.groups = groups

    def locator(self, sel):
        if sel.startswith('[data-automation-id^="formField-"'):
            return _List(self.groups)
        return _List([])


def test_strip_options_tail_leaves_the_question_alone():
    f = filler._strip_options_tail
    assert f("Are you willing to relocate?* Yes No") == "Are you willing to relocate?"
    assert f("Do you have a disability? Yes No Prefer not to say") == \
        "Do you have a disability?"
    # a question that doesn't end in option words is untouched
    assert f("Have you ever been employed by KPMG?") == "Have you ever been employed by KPMG?"


def test_radio_screening_answers_proven_parks_unknown_scoped_clicks(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    auth = _Group("Are you legally authorized to work in the United States?*")
    spons = _Group("Will you require sponsorship for employment?*")
    finra = _Group("Do you currently hold an active FINRA securities license?*")
    seen, answered = filler._fill_radio_screening(_Root([auth, spons, finra]), FACTS)

    assert (seen, answered) == (3, 2)
    assert auth.clicked() == ["Yes"]        # proven fact, clicked in ITS container
    assert spons.clicked() == ["No"]
    assert finra.clicked() == []            # unknown -> untouched...
    parked = qbank.pending()
    assert parked == ["Do you currently hold an active FINRA securities license?"]
    assert "*" not in parked[0] and "Yes" != parked[0][-3:]  # noise stripped for re-match


def test_radio_screening_reuses_the_humans_remembered_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({"Do you currently hold an active FINRA securities license?": "No"})
    finra = _Group("Do you currently hold an active FINRA securities license?")
    seen, answered = filler._fill_radio_screening(_Root([finra]), FACTS)
    assert (seen, answered) == (1, 1)
    assert finra.clicked() == ["No"]
    assert qbank.pending() == []            # nothing re-parked


def test_radio_screening_skips_already_selected_and_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    done = _Group("Are you legally authorized to work in the United States?*", checked="Yes")
    hidden = _Group("Do you hold a FINRA license?*", visible=False)
    seen, answered = filler._fill_radio_screening(_Root([done, hidden]), FACTS)
    assert (seen, answered) == (1, 1)
    assert done.clicked() == []             # left alone on a re-run
    assert qbank.pending() == []            # hidden question never parked


def test_radio_screening_free_text_answer_on_yes_no_group_is_a_miss_not_a_guess(
        tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({"What is your earliest start date?": "2 weeks"})
    start = _Group("What is your earliest start date?*")  # Yes/No options only
    seen, answered = filler._fill_radio_screening(_Root([start]), FACTS)
    assert (seen, answered) == (1, 0)
    assert start.clicked() == []            # '2 weeks' matches no option -> human's turn
    assert qbank.pending() == []            # already answered in qbank, not re-parked


def test_pick_radio_in_group_never_clicks_a_neighboring_question():
    relocate = _Group("Are you willing to relocate for this position?*")
    felony = _Group("Have you ever been convicted of a felony?*")
    root = _Root([felony, relocate])        # felony FIRST — a global click would hit it
    assert filler._pick_radio_in_group(root, "willing to relocate", "Yes")
    assert relocate.clicked() == ["Yes"]
    assert felony.clicked() == []
    # no container matches -> refuses, rather than clicking anything
    assert not filler._pick_radio_in_group(root, "security clearance", "Yes")
