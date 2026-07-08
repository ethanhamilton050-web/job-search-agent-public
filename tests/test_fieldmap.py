"""Field-map cache: signature stability, cache round-trip, reply parsing. No network."""
import pytest

from jobagent import fieldmap

# Real "My Information" descriptors from the Citi run (as _dump_fields logs them).
CITI = [
    'input[radio] id=yq173 name=candidateIsPreviousWorker aid=formField-candidateIsPreviousWorker',
    'button[button] id=country--country name=country aria="Country United States of America Required" aid=formField-country',
    'input[text] id=name--legalName--firstName name=legalName--firstName aid=formField-legalName--firstName',
    'input[text] id=address--city name=city aid=formField-city',
]


def test_signature_ignores_volatile_ids_and_aria():
    # Same structure, but Workday reassigned the random id and the aria value changed.
    other = [
        'input[radio] id=yq999 name=candidateIsPreviousWorker aid=formField-candidateIsPreviousWorker',
        'button[button] id=country--country name=country aria="Country Canada Required" aid=formField-country',
        'input[text] id=name--legalName--firstName name=legalName--firstName aid=formField-legalName--firstName',
        'input[text] id=address--city name=city aid=formField-city',
    ]
    assert fieldmap.signature(CITI) == fieldmap.signature(other)


def test_signature_is_order_independent_but_structure_sensitive():
    assert fieldmap.signature(CITI) == fieldmap.signature(list(reversed(CITI)))
    assert fieldmap.signature(CITI) != fieldmap.signature(CITI[:-1])  # a field removed = different form


def test_cache_round_trip(tmp_path):
    cache = tmp_path / "fieldmaps.json"
    sig = fieldmap.signature(CITI)
    assert fieldmap.get(sig, cache) is None
    mapping = [{"i": 0, "intent": "previous_worker", "strategy": "radio"}]
    fieldmap.put(sig, mapping, cache)
    assert fieldmap.get(sig, cache) == mapping


def test_map_form_serves_cache_without_calling_llm(tmp_path):
    cache = tmp_path / "fieldmaps.json"
    mapping = [{"i": 0, "intent": "city", "strategy": "text"}]
    fieldmap.put(fieldmap.signature(CITI), mapping, cache)
    # allow_llm=True but a cache hit must never touch the network.
    result, source = fieldmap.map_form(CITI, cache_path=cache, base="http://127.0.0.1:9")
    assert source == "cache" and result == mapping


def test_map_form_miss_without_llm_returns_none(tmp_path):
    result, source = fieldmap.map_form(CITI, cache_path=tmp_path / "c.json", allow_llm=False)
    assert result is None and source == "none"


def test_parse_map_extracts_array_from_chatter():
    text = 'Sure! Here is the map:\n[{"i": 0, "intent": "email", "strategy": "text"}]\nHope that helps.'
    assert fieldmap.parse_map(text) == [{"i": 0, "intent": "email", "strategy": "text"}]
    with pytest.raises(ValueError):
        fieldmap.parse_map("no json here")
