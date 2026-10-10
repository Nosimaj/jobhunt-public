import json
from pathlib import Path

import pytest

from jobhunt.discovery.discover import RAW_MARKUP_PREFIX, main
from jobhunt.discovery.discover import HEARTBEAT_DAYS, _month_of
from jobhunt.discovery.store import Store

FIX = Path(__file__).parent / "fixtures" / "discovery"
SEARCH = json.loads((FIX / "hn_story_search.json").read_text(encoding="utf-8"))
THREAD = json.loads((FIX / "hn_thread_49522897.json").read_text(encoding="utf-8"))


def fetcher(url: str) -> dict:
    return SEARCH if "search" in url else THREAD


def seed_successful_run(tmp_path, finished_at, thread_id="49522897"):
    """Write a `runs` row with an EXACT finished_at, and return the store.

    The CLI stamps finished_at from the wall clock via _now(), NOT from
    --today, while the heartbeat age is measured against --today. So the
    heartbeat boundary cannot be expressed through --today alone, and a test
    that tries drifts with the real date: asserting a warning at
    --today=2026-11-05 after a real run only held while the wall clock was
    before 2026-09-26.
    """
    store = Store(tmp_path / "jobs.sqlite")
    store.init_schema()
    rid = store.start_run(finished_at)
    store.finish_run(rid, finished_at=finished_at, thread_id=thread_id,
                     posts_seen=260, candidates=132, rejected=128,
                     new_postings=260)
    return store


def run(tmp_path, *extra, today="2026-09-20"):
    argv = ["--db", str(tmp_path / "jobs.sqlite"),
            "--snapshot-dir", str(tmp_path / "snap"),
            "--today", today, *extra]
    return main(argv, fetch=fetcher)


def test_end_to_end_produces_the_measured_split(tmp_path, capsys):
    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "posts seen : 260" in out
    assert "candidates : 132" in out
    assert "rejected   : 128" in out
    assert "new rows   : 260" in out


def test_run_accounting_balances_on_the_real_fixture(tmp_path):
    """posts_seen == candidates + rejected. A run row that does not account
    for every post it saw is dropping some silently."""
    run(tmp_path)
    r = Store(tmp_path / "jobs.sqlite").last_run()
    assert r.posts_seen == 260
    assert r.candidates == 132
    assert r.rejected == 128
    assert r.posts_seen == r.candidates + r.rejected


def test_run_accounting_balances_when_a_post_fails_to_parse(tmp_path):
    """rejected excluded parse failures, even though those posts ARE stored
    with status='rejected'. A 2-child thread where 1 fails gave
    posts_seen=2, candidates=1, rejected=0 - and 2 != 1 + 0."""
    thread = {"id": "99", "title": "Ask HN: Who is hiring? (September 2026)",
              "created_at": "2026-09-01T15:01:17.000Z",
              "children": [{"id": 1, "text": "Acme | Engineer | Remote US"},
                           {"id": 2, "text": "   <p>  "}]}

    db = tmp_path / "jobs.sqlite"
    argv = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap"),
            "--today", "2026-09-20", "--thread", "99"]
    assert main(argv, fetch=lambda url: thread) == 0

    store = Store(db)
    r = store.last_run()
    assert r.posts_seen == 2
    assert r.candidates == 1
    assert r.rejected == 1
    assert r.posts_seen == r.candidates + r.rejected
    # And the accounting matches what is actually in the table.
    assert store.count_postings() == r.posts_seen
    assert len(store.candidates()) == r.candidates
    assert len(store.rejections()) == r.rejected


def test_all_260_postings_are_stored(tmp_path):
    run(tmp_path)
    store = Store(tmp_path / "jobs.sqlite")
    assert store.count_postings() == 260
    assert len(store.rejections()) == 128
    assert len(store.candidates()) == 132


def test_rerunning_the_same_thread_creates_no_new_rows(tmp_path, capsys):
    run(tmp_path)
    capsys.readouterr()
    run(tmp_path, today="2026-09-27")
    out = capsys.readouterr().out
    assert "new rows   : 0" in out
    store = Store(tmp_path / "jobs.sqlite")
    assert store.count_postings() == 260


def test_rerun_updates_last_seen(tmp_path):
    run(tmp_path)
    run(tmp_path, today="2026-09-27")
    store = Store(tmp_path / "jobs.sqlite")
    sample = store.candidates()[0]
    assert sample.first_seen == "2026-09-20"
    assert sample.last_seen == "2026-09-27"


def test_posted_at_is_the_threads_month(tmp_path):
    run(tmp_path)
    store = Store(tmp_path / "jobs.sqlite")
    assert {p.posted_at for p in store.candidates()} == {"2026-09-01"}


def test_snapshot_is_written(tmp_path):
    run(tmp_path)
    snaps = list((tmp_path / "snap").glob("49522897-*.json"))
    assert len(snaps) == 1


#: Real companies from the September 2026 thread whose slice-1 normalisation
#: is NOT reachable by plain `.casefold()`. Spec section 3.5 requires
#: normalize_company, and times_seen_company keys on its output, so a
#: casefold stand-in would split one company across two identities.
_NORMALISATION_CASES = {
    "Modash.io": "modash io",            # punctuation -> space
    "G-Research": "g research",          # dash -> space
    "We The Flywheel": "we flywheel",    # "the" is a dropped legal-form token
    "*Fastly": "fastly",                 # leading symbol stripped
    "Comity Inc.": "comity",             # "inc" suffix dropped
    "Brain Corp": "brain",               # "corp" suffix dropped
    "Steg.AI": "steg ai",
    "Brilliant.org": "brilliant org",
    "Open Education Applications / Neon": "open education applications neon",
}


def test_company_normalised_uses_slice_one_normalisation(tmp_path):
    """Assert the real normalised values, not just that they are lowercase.

    The previous version asserted only casefolding and the absence of double
    spaces, so replacing normalize_company(...) with plain .casefold() passed
    all 194 tests. 54 of the 260 real companies differ between the two.
    """
    run(tmp_path)
    store = Store(tmp_path / "jobs.sqlite")
    rows = store.candidates() + store.rejections()
    assert len(rows) == 260
    by_company = {p.company: p.company_normalised for p in rows}

    for company, expected in _NORMALISATION_CASES.items():
        assert company in by_company, f"{company!r} missing from the fixture"
        assert by_company[company] == expected, company
        # Each case must actually discriminate, or the test is vacuous again.
        assert expected != company.casefold(), (
            f"{company!r} does not distinguish normalize_company from casefold")

    for p in rows:
        assert p.company_normalised == p.company_normalised.casefold()
        assert "  " not in p.company_normalised
        assert p.company_normalised == p.company_normalised.strip()


def test_normalisation_differs_from_casefold_on_a_fifth_of_the_thread(tmp_path):
    """The measured scale of the difference. If this drops toward zero,
    normalize_company has been weakened or bypassed."""
    run(tmp_path)
    store = Store(tmp_path / "jobs.sqlite")
    rows = store.candidates() + store.rejections()
    differing = [p for p in rows if p.company_normalised != p.company.casefold()]
    assert len(differing) == 54


def test_show_rejections_lists_reasons(tmp_path, capsys):
    run(tmp_path)
    capsys.readouterr()
    run(tmp_path, "--show-rejections")
    out = capsys.readouterr().out
    assert "128 rejected postings" in out
    for reason in ("not_remote", "eu_only", "no_role_signal"):
        assert reason in out


def test_status_reports_the_last_run(tmp_path, capsys):
    run(tmp_path)
    capsys.readouterr()
    run(tmp_path, "--status")
    out = capsys.readouterr().out
    assert "49522897" in out
    assert "260 / 132" in out


def test_status_warns_when_the_last_run_is_older_than_the_heartbeat(tmp_path, capsys):
    seed_successful_run(tmp_path, "2026-09-20T10:00:00")
    capsys.readouterr()
    run(tmp_path, "--status", today="2026-11-05")   # 46 days later
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "46 days ago" in captured.err


def test_status_warns_when_the_most_recent_run_failed(tmp_path, capsys):
    """Continuous failure must not look healthy.

    --status used to read ONLY last_successful_run(), so one success followed
    by any number of hard failures printed a clean report and an empty
    stderr while runs.error went unread. Substituting last_run() for
    last_successful_run() in _status_report passed all 194 tests.
    """
    def boom(url: str) -> dict:
        raise RuntimeError("network down")

    db = tmp_path / "jobs.sqlite"
    base = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap")]
    assert main([*base, "--today", "2026-09-20"], fetch=fetcher) == 0
    with pytest.raises(RuntimeError, match="network down"):
        main([*base, "--today", "2026-09-21"], fetch=boom)
    capsys.readouterr()

    assert main([*base, "--status", "--today", "2026-09-22"], fetch=fetcher) == 0
    captured = capsys.readouterr()
    # The successful run is still reported...
    assert "49522897" in captured.out
    assert "260 / 132" in captured.out
    # ...but the failure is loud, named, and counted.
    assert "WARNING" in captured.err
    assert "FAILED" in captured.err
    assert "network down" in captured.err
    assert "1 run(s) have failed since the last success" in captured.err


def test_status_counts_every_failure_since_the_last_success(tmp_path, capsys):
    def boom(url: str) -> dict:
        raise RuntimeError("network down")

    db = tmp_path / "jobs.sqlite"
    base = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap")]
    main([*base, "--today", "2026-09-20"], fetch=fetcher)
    for _ in range(4):
        with pytest.raises(RuntimeError):
            main([*base, "--today", "2026-09-21"], fetch=boom)
    capsys.readouterr()

    main([*base, "--status", "--today", "2026-10-30"], fetch=fetcher)
    err = capsys.readouterr().err
    assert "4 run(s) have failed since the last success" in err


def test_status_warns_when_every_run_has_failed(tmp_path, capsys):
    def boom(url: str) -> dict:
        raise RuntimeError("network down")

    db = tmp_path / "jobs.sqlite"
    base = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap")]
    with pytest.raises(RuntimeError):
        main([*base, "--today", "2026-09-20"], fetch=boom)
    capsys.readouterr()

    assert main([*base, "--status", "--today", "2026-09-21"], fetch=fetcher) == 0
    captured = capsys.readouterr()
    assert "No successful run recorded yet" in captured.out
    assert "WARNING" in captured.err
    assert "never been a successful run" in captured.err


def test_status_does_not_warn_when_the_last_run_succeeded(tmp_path, capsys):
    run(tmp_path)
    capsys.readouterr()
    run(tmp_path, "--status")
    assert capsys.readouterr().err == ""


def test_status_does_not_warn_within_the_heartbeat_window(tmp_path, capsys):
    seed_successful_run(tmp_path, "2026-09-20T10:00:00")
    capsys.readouterr()
    run(tmp_path, "--status", today="2026-10-10")   # 20 days later
    assert "WARNING" not in capsys.readouterr().err


def test_status_does_not_warn_at_exactly_the_heartbeat_window(tmp_path, capsys):
    """Boundary. The comparison is `age > HEARTBEAT_DAYS`, so a last
    successful run exactly 40 days old does NOT warn.

    Pins two mutations that both passed all 194 tests: HEARTBEAT_DAYS
    40->25, and `age > HEARTBEAT_DAYS` -> `>=`.
    """
    assert HEARTBEAT_DAYS == 40
    seed_successful_run(tmp_path, "2026-09-20T10:00:00")
    capsys.readouterr()
    run(tmp_path, "--status", today="2026-10-30")   # exactly 40 days later
    assert "WARNING" not in capsys.readouterr().err


def test_status_warns_one_day_past_the_heartbeat_window(tmp_path, capsys):
    seed_successful_run(tmp_path, "2026-09-20T10:00:00")
    capsys.readouterr()
    run(tmp_path, "--status", today="2026-10-31")   # 41 days later
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "41 days ago" in err


def test_status_on_a_fresh_database_says_so(tmp_path, capsys):
    run(tmp_path, "--status")
    assert "No successful run recorded yet" in capsys.readouterr().out


def test_thread_flag_backfills_a_named_thread(tmp_path, capsys):
    assert run(tmp_path, "--thread", "49522897") == 0
    assert "posts seen : 260" in capsys.readouterr().out


def test_thread_flag_stamps_the_threads_own_month_not_today(tmp_path):
    """Regression guard: a --thread backfill has no search hit to learn the
    month from, so it must derive posted_at from the item response's OWN
    created_at (September, per the fixture) - never from --today, which
    here is a different month entirely. Fails against the pre-fix code,
    which stamped f"{args.today[:7]}-01" == "2026-11-01".
    """
    assert run(tmp_path, "--thread", "49522897", today="2026-11-15") == 0
    store = Store(tmp_path / "jobs.sqlite")
    assert {p.posted_at for p in store.candidates()} == {"2026-09-01"}


def test_parse_failure_is_persisted_as_a_parse_error_rejection(tmp_path, capsys):
    """A post that fails to parse must still be recorded, not silently
    dropped - it lands as a rejected posting with reject_reason
    "parse_error" and shows up under --show-rejections."""
    thread = {"id": "99", "title": "Ask HN: Who is hiring? (September 2026)",
             "created_at": "2026-09-01T15:01:17.000Z",
             "children": [{"id": 1, "text": "   "}]}

    def bad_fetcher(url: str) -> dict:
        return thread

    db = tmp_path / "jobs.sqlite"
    argv = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap"),
            "--today", "2026-09-20", "--thread", "99"]
    assert main(argv, fetch=bad_fetcher) == 0

    store = Store(db)
    row = store.get("hn:1")
    assert row is not None
    assert row.status == "rejected"
    assert row.reject_reason == "parse_error"
    assert row.company == "<unparsed>"

    capsys.readouterr()
    assert main(["--db", str(db), "--show-rejections"], fetch=bad_fetcher) == 0
    out = capsys.readouterr().out
    assert "1 rejected postings" in out
    assert "parse_error" in out


def test_parse_failure_stores_cleaned_text_when_only_company_extraction_failed(tmp_path):
    """raw_text must keep ONE format. Cleaning succeeded here - only
    company_of returned empty - so the row holds cleaned plain text like
    every other row, not raw HN markup.
    """
    thread = {"id": "99", "title": "Ask HN: Who is hiring? (September 2026)",
              "created_at": "2026-09-01T15:01:17.000Z",
              "children": [{"id": 1, "text": "<p>---<p>We need an engineer. Remote US."}]}

    db = tmp_path / "jobs.sqlite"
    argv = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap"),
            "--today", "2026-09-20", "--thread", "99"]
    assert main(argv, fetch=lambda url: thread) == 0

    row = Store(db).get("hn:1")
    assert row.reject_reason == "parse_error"
    assert "<p>" not in row.raw_text
    assert not row.raw_text.startswith(RAW_MARKUP_PREFIX)
    assert "We need an engineer. Remote US." in row.raw_text


def test_parse_failure_flags_raw_markup_only_when_cleaning_produced_nothing(tmp_path):
    """The one case where there is no cleaned text to store. Previously
    EVERY parse failure stored bare markup ('<p>   <p> '), so raw_text had
    two incompatible formats keyed on status."""
    thread = {"id": "99", "title": "Ask HN: Who is hiring? (September 2026)",
              "created_at": "2026-09-01T15:01:17.000Z",
              "children": [{"id": 1, "text": "<p>   <p> "}]}

    db = tmp_path / "jobs.sqlite"
    argv = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap"),
            "--today", "2026-09-20", "--thread", "99"]
    assert main(argv, fetch=lambda url: thread) == 0

    row = Store(db).get("hn:1")
    assert row.reject_reason == "parse_error"
    assert row.raw_text.startswith(RAW_MARKUP_PREFIX)
    assert "<p>" in row.raw_text          # the raw markup is preserved verbatim


def test_no_stored_row_contains_raw_html_unless_it_is_flagged(tmp_path):
    """The format guarantee, asserted across every row of the real thread."""
    run(tmp_path)
    for row in Store(tmp_path / "jobs.sqlite").candidates():
        assert "<p>" not in row.raw_text and "&#x" not in row.raw_text


def test_a_failing_run_is_recorded_before_the_error_propagates(tmp_path):
    def boom(url: str) -> dict:
        raise RuntimeError("network down")
    argv = ["--db", str(tmp_path / "jobs.sqlite"),
            "--snapshot-dir", str(tmp_path / "snap"), "--today", "2026-09-20"]
    with pytest.raises(RuntimeError, match="network down"):
        main(argv, fetch=boom)
    run_row = Store(tmp_path / "jobs.sqlite").last_run()
    assert run_row.error is not None and "network down" in run_row.error


def test_show_rejections_survives_a_null_reject_reason(tmp_path, capsys):
    """A slice-3 `set_status(id, "rejected")` leaves reject_reason NULL, and
    formatting None with `{:15s}` is a TypeError - raised AFTER the count
    line had already printed, so the command half-worked and then blew up.
    """
    run(tmp_path)
    db = tmp_path / "jobs.sqlite"
    store = Store(db)
    target = store.candidates()[0].id
    store.set_status(target, "rejected")          # no reason recorded
    capsys.readouterr()

    assert main(["--db", str(db), "--show-rejections"], fetch=fetcher) == 0
    out = capsys.readouterr().out
    assert "129 rejected postings" in out
    assert target in out
    assert "[-" in out                            # the placeholder, not a crash


def test_company_normalised_is_never_empty_even_when_normalisation_clears_it(tmp_path):
    """`company` is guaranteed non-empty and that guarantee must carry across
    to the indexed column slice 5 keys on. A first segment of "//" normalises
    to "", and "The Group" is all legal-form tokens so it does too.
    """
    thread = {"id": "99", "title": "Ask HN: Who is hiring? (September 2026)",
              "created_at": "2026-09-01T15:01:17.000Z",
              "children": [{"id": 1, "text": "// | Engineer | Remote US"},
                           {"id": 2, "text": "The Group | Engineer | Remote US"}]}

    db = tmp_path / "jobs.sqlite"
    argv = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap"),
            "--today", "2026-09-20", "--thread", "99"]
    assert main(argv, fetch=lambda url: thread) == 0

    store = Store(db)
    slashes, group = store.get("hn:1"), store.get("hn:2")
    assert slashes.company == "//"
    assert slashes.company_normalised == "hn:1"        # id fallback
    assert group.company == "The Group"
    assert group.company_normalised == "the-group"     # slug fallback
    for row in (slashes, group):
        assert row.company_normalised.strip()


def test_month_of_normalises_any_day_to_the_first(tmp_path):
    """`created_at[:10]` passed everything, because the fixture thread was
    created on the 1st and so [:10] and [:7]+"-01" agree there."""
    assert _month_of("2026-09-15T15:01:17.000Z") == "2026-09-01"
    assert _month_of("2026-12-31T23:59:59.000Z") == "2026-12-01"
    assert _month_of("2026-01-02T00:00:00.000Z") == "2026-01-01"
    assert _month_of("2026-09-01T15:01:17.000Z") == "2026-09-01"


def test_a_mid_month_thread_is_stamped_to_the_first(tmp_path):
    thread = {"id": "99", "title": "Ask HN: Who is hiring? (September 2026)",
              "created_at": "2026-09-17T15:01:17.000Z",
              "children": [{"id": 1, "text": "Acme | Engineer | Remote US"}]}
    db = tmp_path / "jobs.sqlite"
    argv = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap"),
            "--today", "2026-09-20", "--thread", "99"]
    assert main(argv, fetch=lambda url: thread) == 0
    assert Store(db).get("hn:1").posted_at == "2026-09-01"


def test_the_default_path_takes_posted_at_from_the_item_response(tmp_path):
    """Both paths must derive posted_at from the ITEM response.

    The default path read it off the search hit and then called
    fetch_thread_with_meta and discarded the ref, so the two sources could
    disagree unnoticed. Here they disagree by construction: the search hit
    says October, the item response says September.
    """
    search = {"hits": [{"objectID": "49522897",
                        "title": "Ask HN: Who is hiring? (October 2026)",
                        "created_at": "2026-10-03T15:01:17.000Z",
                        "author": "whoishiring", "num_comments": 402}]}

    def disagreeing_fetcher(url: str) -> dict:
        return search if "search" in url else THREAD      # THREAD is September

    db = tmp_path / "jobs.sqlite"
    argv = ["--db", str(db), "--snapshot-dir", str(tmp_path / "snap"),
            "--today", "2026-10-20"]
    assert main(argv, fetch=disagreeing_fetcher) == 0
    assert {p.posted_at for p in Store(db).candidates()} == {"2026-09-01"}


def test_downstream_status_survives_a_repoll(tmp_path):
    run(tmp_path)
    store = Store(tmp_path / "jobs.sqlite")
    target = store.candidates()[0].id
    store.set_status(target, "approved_by_slice_5")
    run(tmp_path, today="2026-09-27")
    assert Store(tmp_path / "jobs.sqlite").get(target).status == "approved_by_slice_5"


def test_a_poll_harvests_ats_tokens(tmp_path):
    """HN's new job. It is no longer the role source; it is the only thing
    that discovers which companies exist.

    `run` and `fetcher` are the helpers already defined at the top of this
    module; the committed September thread is the fixture behind them.
    """
    from jobhunt.discovery.store import Store

    run(tmp_path)

    boards = Store(tmp_path / "jobs.sqlite").companies_with_boards()
    # Committed fixture: 42 ashby/greenhouse/lever tokens, +4 workable/teamtailor.
    assert len(boards) == 46
    assert all(p in ("greenhouse", "ashby", "lever", "workable", "teamtailor")
               for _, _, p, _ in boards)


def test_harvesting_is_idempotent_across_two_polls(tmp_path):
    from jobhunt.discovery.store import Store

    run(tmp_path)
    run(tmp_path)

    assert len(Store(tmp_path / "jobs.sqlite").companies_with_boards()) == 46
