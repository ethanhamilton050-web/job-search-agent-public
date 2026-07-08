"""PII scrubber: pre-filled values must never leave the device, only blank structure.

The scrubber is the privacy promise made concrete — verifies value= is dropped and
answer-bank PII is redacted, while the structural identifiers the model needs survive.
"""
from jobagent import fieldmap

# A real name/email/phone riding along in descriptors, as a live page would carry them.
ANSWERS = {
    "first_name": "Ethan",
    "last_name": "Hamilton",
    "email": "you@example.com",
    "phone": "5551234567",
    "is_over_18": True,          # bool: must NOT be used for redaction
    "needs_sponsorship": False,
    "salary_expectation": 80000,  # int: must NOT be used for redaction
    "voluntary_self_id": {"gender": "decline"},
    "experience": [{"company": "Goldman Sachs", "title": "Analyst"}],
}


def test_strips_quoted_value_attribute():
    d = ['input[text] id=name--firstName name=legalName--firstName value="Ethan" aid=formField-firstName']
    out = fieldmap.scrub(d)
    assert "Ethan" not in out[0]
    assert "value=" not in out[0]
    # structure survives
    assert "id=name--firstName" in out[0]
    assert "name=legalName--firstName" in out[0]
    assert "aid=formField-firstName" in out[0]


def test_strips_unquoted_value_attribute():
    d = ['input[text] id=address--city name=city value=Newark aid=formField-city']
    out = fieldmap.scrub(d)
    assert "Newark" not in out[0]
    assert "value=" not in out[0]
    assert "name=city" in out[0] and "aid=formField-city" in out[0]


def test_redacts_answer_values_from_descriptor():
    d = [
        'input[text] id=name--firstName name=legalName--firstName value="Ethan" aid=formField-firstName',
        'input[text] id=email name=email value=you@example.com aid=formField-email',
        'input[text] id=phone name=phoneNumber value=5551234567 aid=formField-phoneNumber',
    ]
    out = fieldmap.scrub(d, ANSWERS)
    joined = "\n".join(out)
    assert "Ethan" not in joined
    assert "you@example.com" not in joined
    assert "5551234567" not in joined
    # meaning-bearing structure is untouched
    assert "name=legalName--firstName" in out[0]
    assert "aid=formField-email" in out[1]
    assert "aid=formField-phoneNumber" in out[2]


def test_redacts_pii_from_aria_but_keeps_the_label_meaning():
    # aria carries the field's MEANING (State) plus a leaked value (the city). Keep the
    # meaning, redact the value — never blanket-strip aria.
    d = ['button[button] id=address--city name=city aria="City Newark Required" aid=formField-city']
    out = fieldmap.scrub(d, {"city": "Newark"})
    assert "Newark" not in out[0]
    assert "aria=" in out[0]          # aria kept
    assert "City" in out[0]           # label meaning kept
    assert "Required" in out[0]


def test_does_not_redact_boolean_or_numeric_values():
    # A structural token that happens to contain "18" or "No" must not be gutted by
    # redacting the is_over_18=True / salary=80000 answers.
    d = ['input[text] id=field18 name=question18 aid=formField-question18']
    out = fieldmap.scrub(d, ANSWERS)
    assert out[0] == d[0]  # nothing string-PII matched -> untouched


def test_blank_descriptor_is_a_no_op():
    # No value=, no PII present -> scrub returns it unchanged (both with and without answers).
    blank = [
        'input[text] id=name--legalName--firstName name=legalName--firstName aid=formField-legalName--firstName',
        'input[radio] id=yq173 name=candidateIsPreviousWorker aid=formField-candidateIsPreviousWorker',
    ]
    assert fieldmap.scrub(blank) == blank
    assert fieldmap.scrub(blank, ANSWERS) == blank


def test_scrub_preserves_signature_for_structurally_identical_forms():
    # Two renders of the same page: one still carries pre-filled values, one is blank.
    # After scrubbing, both must key to the same cache signature.
    filled = [
        'input[text] id=name--firstName name=legalName--firstName value="Ethan" aid=formField-firstName',
        'input[text] id=address--city name=city value=Newark aid=formField-city',
    ]
    blank = [
        'input[text] id=name--firstName name=legalName--firstName aid=formField-firstName',
        'input[text] id=address--city name=city aid=formField-city',
    ]
    assert fieldmap.signature(fieldmap.scrub(filled, ANSWERS)) == fieldmap.signature(fieldmap.scrub(blank))
    # And scrubbing an already-blank form does not perturb its signature.
    assert fieldmap.signature(fieldmap.scrub(blank)) == fieldmap.signature(blank)


def test_call_llm_scrubs_before_sending(monkeypatch):
    # The value must never reach the wire: capture the prompt call_llm builds.
    sent = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"message": {"content": "[{\\"i\\": 0, \\"intent\\": \\"first_name\\", \\"strategy\\": \\"text\\"}]"}}'

    def _fake_urlopen(req, timeout=180):
        sent["body"] = req.data.decode("utf-8")
        return _FakeResp()

    monkeypatch.setattr(fieldmap.urllib.request, "urlopen", _fake_urlopen)
    d = ['input[text] id=name--firstName name=legalName--firstName value="Ethan" aid=formField-firstName']
    fieldmap.call_llm(d, "gemma2", "http://localhost:11434", answers=ANSWERS)
    assert "Ethan" not in sent["body"]
    assert "value=" not in sent["body"]
    assert "legalName--firstName" in sent["body"]  # structure did reach the model
