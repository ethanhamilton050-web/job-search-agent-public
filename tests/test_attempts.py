"""Per-company auto-apply safety cap."""
import sqlite3

from jobagent import attempts


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    attempts.ensure_table(conn)
    return conn


def test_cap_blocks_after_three_attempts():
    conn = _db()
    for _ in range(attempts.CAP):
        assert attempts.allowed(conn, "Citi")
        attempts.record(conn, "Citi")
    assert attempts.count(conn, "Citi") == attempts.CAP
    assert not attempts.allowed(conn, "Citi")  # the 4th attempt is refused


def test_company_key_is_normalized():
    conn = _db()
    attempts.record(conn, "Citi")
    attempts.record(conn, "  citi ")  # same company, different spacing/case
    assert attempts.count(conn, "CITI") == 2


def test_other_companies_are_independent():
    conn = _db()
    for _ in range(attempts.CAP):
        attempts.record(conn, "Citi")
    assert not attempts.allowed(conn, "Citi")
    assert attempts.allowed(conn, "Stripe")  # a different employer is unaffected


def test_blank_company_is_never_capped():
    conn = _db()
    assert attempts.allowed(conn, "")
    assert attempts.record(conn, "") == 0  # nothing recorded for an unknown company
    assert attempts.allowed(conn, "")


def test_try_record_is_atomic_check_and_increment():
    conn = _db()
    for _ in range(attempts.CAP):
        assert attempts.try_record(conn, "Citi")
    assert attempts.count(conn, "Citi") == attempts.CAP
    # the cap-hitting attempt is refused AND nothing gets recorded for it
    assert not attempts.try_record(conn, "Citi")
    assert attempts.count(conn, "Citi") == attempts.CAP


def test_try_record_repeated_calls_never_exceed_cap():
    conn = _db()
    results = [attempts.try_record(conn, "Citi") for _ in range(attempts.CAP + 2)]
    assert results == [True, True, True, False, False]
    assert attempts.count(conn, "Citi") == attempts.CAP


def test_try_record_survives_real_concurrent_processes(tmp_path):
    """Regression (found live, 2026-07-09, by an overnight adversarial audit):
    allowed()+record() as two separate DB round-trips lets two concurrent
    processes each read the same stale under-cap count and both then record,
    pushing the total past CAP -- exactly the race a manual Apply click and an
    unattended queue run (or two rapid clicks) can hit in real use. Proves the
    fix with GENUINE concurrency: N real threads, each its own sqlite3
    connection to the same on-disk file (not the ":memory:" fixture other tests
    use here, which isn't shared across connections), all hammering
    try_record() for the same company at once. If the check-and-increment
    weren't atomic, this reliably pushes the count above CAP; with the fix it
    must not, no matter how many threads race."""
    import threading

    db_path = tmp_path / "attempts.db"
    n_workers = 20

    def worker(results, i):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            results[i] = attempts.try_record(conn, "Citi")
        finally:
            conn.close()

    results = [None] * n_workers
    threads = [threading.Thread(target=worker, args=(results, i)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    final_count = attempts.count(conn, "Citi")
    conn.close()

    assert final_count == attempts.CAP  # never exceeded, regardless of scheduling
    assert sum(1 for r in results if r) == attempts.CAP  # exactly CAP callers "won"


def test_try_record_blank_company_never_capped():
    conn = _db()
    assert attempts.try_record(conn, "")
    assert attempts.count(conn, "") == 0


def test_reset_clears_the_cap():
    conn = _db()
    for _ in range(attempts.CAP):
        attempts.record(conn, "Citi")
    attempts.reset(conn, "Citi")
    assert attempts.allowed(conn, "Citi") and attempts.count(conn, "Citi") == 0


def test_company_key_unifies_legal_suffix_spellings():
    """'Citi' / 'citi, N.A.' / 'CITI Inc.' are ONE employer and share ONE cap
    budget (ISSUES G: exact-match keys gave each spelling its own CAP=3)."""
    conn = _db()
    assert attempts.try_record(conn, "Citi")
    assert attempts.try_record(conn, "citi, N.A.")
    assert attempts.try_record(conn, "CITI Inc.")
    assert not attempts.try_record(conn, "Citi")   # cap of 3 reached across spellings
    assert attempts.count(conn, "citi n.a.") == 3


def test_company_key_does_not_over_merge_different_companies():
    conn = _db()
    for _ in range(attempts.CAP):
        assert attempts.try_record(conn, "First National Corp")
    # a DIFFERENT company is never blocked by a similar-looking one
    assert attempts.try_record(conn, "First Republic Corp")
    # 'Citigroup' vs 'Citi' deliberately stay separate (no fuzzy matching)
    assert attempts._key("Citigroup") != attempts._key("Citi")


def test_company_key_never_normalizes_to_nothing():
    # A company NAMED a suffix word must not become the blank never-capped key.
    assert attempts._key("Co") == "co"
    assert attempts._key("The Group") != ""  # still capped under SOME stable key


def test_ensure_table_migrates_old_style_keys():
    """Rows recorded before suffix-stripping keep counting toward the same
    company's cap instead of becoming invisible (a silent cap reset)."""
    conn = _db()
    conn.execute("INSERT INTO company_attempts (company, count, last) "
                 "VALUES ('citi, n.a.', 2, '2026-07-01')")
    conn.commit()
    attempts.ensure_table(conn)
    assert attempts.count(conn, "Citi") == 2
    assert attempts.try_record(conn, "Citi")       # 3rd and last slot
    assert not attempts.try_record(conn, "Citi")
