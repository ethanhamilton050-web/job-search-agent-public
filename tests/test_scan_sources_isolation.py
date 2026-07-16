"""_scan_sources fans out one task per configured board; a crash in ANY one of
them must not abort the others -- found live, 2026-07-09, by an overnight
adversarial audit: fut.result() re-raised uncaught, so a single bad board (any
of the several now-fixed source crash scenarios) killed the whole multi-board
scan instead of just that board.
"""
from jobagent.models import Listing


def test_one_crashing_board_does_not_abort_the_others(monkeypatch, capsys):
    import main
    from jobagent.sources import greenhouse, lever

    cfg = {
        "sources": {
            "greenhouse_boards": ["good"],
            "lever_boards": ["bad"],
            "workday_sites": [],
        },
        "targets": {},
    }

    def good_fetch(board):
        return [Listing(title="Analyst", company=board, description="d", url="https://x/1")]

    def bad_fetch(board):
        raise RuntimeError("simulated crash in one board's fetch")

    monkeypatch.setattr(greenhouse, "fetch", good_fetch)
    monkeypatch.setattr(lever, "fetch", bad_fetch)

    found = main._scan_sources(cfg)
    assert len(found) == 1  # the good board's listing survived
    assert found[0].company == "good"
    assert "crashed" in capsys.readouterr().out
