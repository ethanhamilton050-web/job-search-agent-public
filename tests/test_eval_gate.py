"""Guards the Module-0 eval GATE in experiments/field_map_test.py.

The bars turn a score into PASS/FAIL, and a single SAFETY violation (a
fabrication) fails the run no matter how high the capability scores are.
"""
from experiments.field_map_test import (
    FIXTURE, HONESTY_EXPECTED, SCREENING,
    gate_fieldmap, gate_honesty, gate_screening,
    score, score_honesty, score_screening,
)


def test_perfect_map_passes():
    perfect = {i: (gi, gs) for i, (_, gi, gs) in enumerate(FIXTURE)}
    assert gate_fieldmap(score(perfect))["pass"]


def test_map_below_bar_fails():
    bad = {i: ("ignore", "ignore") for i in range(len(FIXTURE))}
    assert not gate_fieldmap(score(bad))["pass"]


def test_perfect_screening_passes():
    perfect = {i: gt for i, (_, gt) in enumerate(SCREENING)}
    assert gate_screening(score_screening(perfect))["pass"]


def test_honesty_is_zero_tolerance():
    flagged = {i: ("NEEDS_HUMAN", "none") for i in HONESTY_EXPECTED}
    assert gate_honesty(score_honesty(flagged))["pass"]  # nothing fabricated -> PASS
    fab = dict(flagged)
    fab[8] = ("No", "Legally authorized to work in the US")  # real line, wrong question
    assert not gate_honesty(score_honesty(fab))["pass"]     # one fabrication -> FAIL
