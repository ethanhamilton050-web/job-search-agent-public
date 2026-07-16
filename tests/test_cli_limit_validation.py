"""A negative --limit used to silently mis-slice a result list (`rows[:-5]`
drops the last 5 rows) instead of erroring on a nonsensical value. Found live,
2026-07-09, by an overnight adversarial audit.
"""
import pytest

import main


def test_non_negative_int_rejects_negative_values():
    with pytest.raises(main.argparse.ArgumentTypeError):
        main._non_negative_int("-5")


def test_non_negative_int_accepts_zero_and_positive():
    assert main._non_negative_int("0") == 0
    assert main._non_negative_int("30") == 30


def test_list_command_rejects_a_negative_limit():
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["list", "--limit", "-5"])


def test_summarize_command_rejects_a_negative_limit():
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["summarize", "--limit", "-5"])


def test_list_command_still_accepts_a_normal_limit():
    args = main.build_parser().parse_args(["list", "--limit", "10"])
    assert args.limit == 10
