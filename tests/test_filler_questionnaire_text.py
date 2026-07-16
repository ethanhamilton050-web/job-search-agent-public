"""Free-text questionnaire questions (Workday textareas: 'reasons for leaving',
'salary expectations') must follow the SAME honesty contract as the Yes/No dropdowns:
fill only from the human's remembered answer (qbank), otherwise PARK to /answers and
leave blank -- never fabricate, and never silently skip.

Found live 2026-07-13 on the PNC Financial Advisor form: _fill_questionnaire looped
only over dropdown BUTTONS, so required textareas were neither filled nor parked -- a
silent required-field stall that stopped the wizard reaching Review. Fake locators
mirror the Playwright surface the code touches (same style as test_filler_radio_screening).
"""
from jobagent import qbank
from jobagent.workday import filler

FACTS = {"work_authorized": True, "needs_sponsorship": False,
         "is_over_18": True, "is_veteran": False}


class _Container:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class _TextArea:
    def __init__(self, question, value="", visible=True):
        self.question, self.value, self.visible = question, value, visible

    def is_visible(self):
        return self.visible

    def input_value(self):
        return self.value

    def fill(self, text):
        self.value = text

    def locator(self, sel):
        # the code asks for the ancestor formField- container's text
        return _Container(f"{self.question}* ")


class _List:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]


class _Root:
    def __init__(self, textareas):
        self.textareas = textareas

    def locator(self, sel):
        if sel.startswith("textarea"):
            return _List(self.textareas)
        return _List([])


def test_unanswered_free_text_is_parked_not_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    salary = _TextArea("Please provide your salary expectations for this position.")
    reasons = _TextArea("Please provide reasons for leaving previous employers.")
    filled = filler._fill_questionnaire_text(_Root([salary, reasons]), FACTS)

    assert filled == 0                       # nothing fabricated
    assert salary.value == "" and reasons.value == ""
    parked = sorted(qbank.pending())
    assert parked == [
        "Please provide reasons for leaving previous employers.",
        "Please provide your salary expectations for this position.",
    ]
    assert all("*" not in p for p in parked)  # noise stripped so it re-matches next run


def test_remembered_free_text_answer_is_filled(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({"Please provide your salary expectations for this position.": "$95,000"})
    salary = _TextArea("Please provide your salary expectations for this position.")
    filled = filler._fill_questionnaire_text(_Root([salary]), FACTS)

    assert filled == 1
    assert salary.value == "$95,000"         # typed the human's saved answer
    assert qbank.pending() == []             # nothing re-parked


def test_salary_strategy_without_a_range_parks_never_fills_the_word(tmp_path, monkeypatch):
    # 2026-07-13 live bug (PNC, no posted range): salary was answered "average", so the
    # box got the literal word "average". It must PARK (stay blank) instead.
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({"Please provide your salary expectations for this position.": "average"})
    salary_box = _TextArea("Please provide your salary expectations for this position.")
    filled = filler._fill_questionnaire_text(_Root([salary_box]),
                                             {"_job": {"salary_range": None}})
    assert filled == 0
    assert salary_box.value == ""            # NOT "average"


def test_salary_strategy_with_a_range_fills_the_number(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    qbank.save({"Please provide your salary expectations for this position.": "average"})
    salary_box = _TextArea("Please provide your salary expectations for this position.")
    filled = filler._fill_questionnaire_text(_Root([salary_box]),
                                             {"_job": {"salary_range": (80000, 100000)}})
    assert filled == 1
    assert salary_box.value == "90000"       # computed number, not the word


def test_already_filled_textarea_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(qbank, "STORE", tmp_path / "s.json")
    prefilled = _TextArea("Salary expectations?", value="$95,000")
    hidden = _TextArea("Reasons for leaving?", visible=False)
    filled = filler._fill_questionnaire_text(_Root([prefilled, hidden]), FACTS)

    assert filled == 1                       # counted, but not overwritten
    assert prefilled.value == "$95,000"
    assert qbank.pending() == []             # hidden question never parked
