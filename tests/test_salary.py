"""Salary-expectation math: parse a posted range from text, map below/average/above to
a number, and a grounded median fallback estimate. Guards the 2026-07-13 'smart salary'
feature (Ethan's design: a reusable strategy, not a fixed dollar figure)."""
from jobagent import salary


def test_parse_explicit_range():
    assert salary.parse_range("Pay range: $80,000 - $100,000 per year") == (80000, 100000)
    assert salary.parse_range("$80,000–$100,000") == (80000, 100000)   # en-dash
    assert salary.parse_range("$80,000 to $100,000") == (80000, 100000)
    assert salary.parse_range("$80K-$100K") == (80000, 100000)


def test_parse_single_figure():
    assert salary.parse_range("The base salary is $95,000.") == (95000, 95000)


def test_parse_rejects_non_salary_numbers():
    assert salary.parse_range("Call 555-555-0100 for details") is None
    assert salary.parse_range("a $5,000 signing bonus") is None      # below annual floor
    assert salary.parse_range("Founded in 2019, 500 employees") is None
    assert salary.parse_range("") is None
    assert salary.parse_range(None) is None


def test_parse_orders_low_high():
    assert salary.parse_range("$100,000 - $80,000") == (80000, 100000)  # reversed input


def test_pick_maps_strategy_to_number():
    assert salary.pick(80000, 100000, "below") == 80000
    assert salary.pick(80000, 100000, "average") == 90000
    assert salary.pick(80000, 100000, "above") == 100000
    assert salary.pick(80000, 100000, "AVERAGE") == 90000       # case-insensitive
    assert salary.pick(80000, 100000, "") == 90000              # unknown -> average default
    assert salary.pick(85000, 85000, "above") == 85000          # single-figure range


def test_estimate_is_median_and_needs_enough_data():
    assert salary.estimate_range([(80000, 100000), (90000, 110000),
                                  (100000, 120000)]) == (90000, 110000)
    assert salary.estimate_range([(90000, 90000)]) is None      # too few to trust
    assert salary.estimate_range([]) is None
    assert salary.estimate_range([None, (80000, 100000)]) is None  # one usable -> too few
