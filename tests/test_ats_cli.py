"""`python -m jobhunt.ats` - walk the registry, poll every board."""

import json
import sqlite3
from pathlib import Path

from jobhunt.ats.poll import main
from jobhunt.discovery.store import Store

FIX = Path(__file__).parent / "fixtures" / "ats"


def registry(tmp_path, entries=(("runway", "Runway", "ashby", "runway-ml"),)):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    for norm, name, provider, token in entries:
        s.set_ats_token(norm, name, provider, token)
    return s


def fake_fetch(calls=None):
    payload = json.loads((FIX / "ashby-runway.json").read_text())
    def _fetch(url):
        if calls is not None:
            calls.append(url)
        return payload
    return _fetch


def run(tmp_path, *extra, fetch=None):
    return main(["--db", str(tmp_path / "j.sqlite"), *extra],
                fetch=fetch or fake_fetch())


def test_polling_stores_roles(tmp_path):
    registry(tmp_path)
    assert run(tmp_path) == 0
    assert Store(tmp_path / "j.sqlite").count_postings() > 0


def test_every_registered_board_is_polled(tmp_path):
    registry(tmp_path, [("a", "A", "ashby", "a-co"), ("b", "B", "ashby", "b-co")])
    calls = []
    run(tmp_path, fetch=fake_fetch(calls))
    assert len(calls) == 2


def test_one_dead_board_does_not_stop_the_others(tmp_path):
    """A token scraped from prose can be wrong - jobs.lever.co/checkly 404s
    because Checkly is on Ashby. 41 boards must still run."""
    registry(tmp_path, [("a", "A", "ashby", "a-co"), ("b", "B", "ashby", "b-co")])
    payload = json.loads((FIX / "ashby-runway.json").read_text())
    def flaky(url):
        if "a-co" in url:
            raise RuntimeError("HTTP 404")
        return payload
    assert run(tmp_path, fetch=flaky) == 0
    assert Store(tmp_path / "j.sqlite").count_postings() > 0


def test_a_board_failure_is_recorded_against_the_company(tmp_path):
    registry(tmp_path, [("a", "A", "ashby", "a-co")])
    def dead(url):
        raise RuntimeError("HTTP 404")
    run(tmp_path, fetch=dead)
    import sqlite3
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    err = conn.execute("SELECT ats_last_error FROM companies").fetchone()[0]
    assert "404" in err


def test_a_successful_board_clears_a_previous_error(tmp_path):
    registry(tmp_path)
    run(tmp_path, fetch=lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
    run(tmp_path)
    import sqlite3
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    assert conn.execute("SELECT ats_last_error FROM companies").fetchone()[0] is None


def test_the_run_is_recorded_with_its_own_kind(tmp_path):
    """Slice 2's heartbeat asks 'when did a poll last succeed?'. An ATS run
    answering that question is the exact bug runs.kind was added to prevent."""
    registry(tmp_path)
    run(tmp_path)
    s = Store(tmp_path / "j.sqlite")
    assert s.last_run(Store.ATS) is not None
    assert s.last_run(Store.DISCOVERY) is None


def test_status_reports_the_registry_without_polling(tmp_path):
    registry(tmp_path)
    calls = []
    assert run(tmp_path, "--status", fetch=fake_fetch(calls)) == 0
    assert calls == []


def test_dry_run_fetches_but_stores_nothing(tmp_path):
    registry(tmp_path)
    calls = []
    assert run(tmp_path, "--dry-run", fetch=fake_fetch(calls)) == 0
    assert len(calls) == 1
    assert Store(tmp_path / "j.sqlite").count_postings() == 0


def test_limit_polls_only_the_first_n_boards(tmp_path):
    registry(tmp_path, [("a", "A", "ashby", "a-co"),
                        ("b", "B", "ashby", "b-co"),
                        ("c", "C", "ashby", "c-co")])
    calls = []
    run(tmp_path, "--limit", "2", fetch=fake_fetch(calls))
    assert len(calls) == 2


def _ashby_job(job_id, *, location="Remote", body_len=250):
    return {
        "id": job_id,
        "title": f"Role {job_id}",
        "department": "Engineering",
        "team": "Engineering",
        "location": location,
        "isRemote": location == "Remote",
        "workplaceType": "Remote" if location == "Remote" else "Onsite",
        "publishedAt": "2026-09-01T00:00:00.000+00:00",
        "applyUrl": f"https://jobs.ashbyhq.com/co/{job_id}/application",
        "descriptionPlain": "x" * body_len,
    }


def test_non_storable_roles_are_filtered_out_before_upsert(tmp_path):
    """`upsert_ats_role` RAISES ValueError for a non-storable role rather
    than returning False, so the CLI must filter with `is_storable` first.
    A foreign location ("London, UK") and a body under 200 chars must both
    be dropped silently, alongside one good role that IS stored."""
    registry(tmp_path)
    payload = {"jobs": [
        _ashby_job("good-1", location="Remote", body_len=250),
        _ashby_job("foreign-1", location="London, UK", body_len=250),
        _ashby_job("short-1", location="Remote", body_len=50),
    ]}
    assert run(tmp_path, fetch=lambda url: payload) == 0
    store = Store(tmp_path / "j.sqlite")
    assert store.count_postings() == 1
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    title = conn.execute("SELECT role_title FROM postings").fetchone()[0]
    assert title == "Role good-1"


def test_run_accounting_balances_seen_candidates_and_rejected(tmp_path):
    """Slice 2's convention: posts_seen == candidates + rejected, so a run
    row can tell '40 fetched, 37 filtered' apart from '3 fetched'."""
    registry(tmp_path)
    payload = {"jobs": [
        _ashby_job("good-1", location="Remote", body_len=250),
        _ashby_job("good-2", location="Remote", body_len=250),
        _ashby_job("foreign-1", location="London, UK", body_len=250),
        _ashby_job("short-1", location="Remote", body_len=50),
    ]}
    assert run(tmp_path, fetch=lambda url: payload) == 0
    s = Store(tmp_path / "j.sqlite")
    run_row = s.last_run(Store.ATS)
    assert run_row.posts_seen == 4
    assert run_row.candidates == 2
    assert run_row.rejected == 2
    assert run_row.posts_seen == run_row.candidates + run_row.rejected


def test_status_flags_a_poll_in_which_every_board_failed(tmp_path, capsys):
    """The same total-outage-looks-healthy failure `runs.kind` was introduced
    to prevent. Printing only `finished_at` made 42-of-42 boards failing look
    like a clean poll, and the other two CLIs both flag it."""
    registry(tmp_path, [("a", "A", "ashby", "a-co"), ("b", "B", "ashby", "b-co")])
    def dead(url):
        raise RuntimeError("HTTP 404")
    run(tmp_path, fetch=dead)
    capsys.readouterr()

    run(tmp_path, "--status", fetch=dead)

    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "2 board(s) failed" in out
    assert "last successful poll" in out
    assert "none" in out.split("last successful poll")[1]


def test_status_reports_the_last_success_separately_from_the_last_attempt(
        tmp_path, capsys):
    registry(tmp_path)
    run(tmp_path)                                    # succeeds
    run(tmp_path, fetch=lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
    capsys.readouterr()

    run(tmp_path, "--status")

    out = capsys.readouterr().out
    assert "FAILED" in out
    success_line = out.split("last successful poll")[1].splitlines()[0]
    assert "none" not in success_line


def test_status_on_a_healthy_poll_says_nothing_about_failure(tmp_path, capsys):
    registry(tmp_path)
    run(tmp_path)
    capsys.readouterr()

    run(tmp_path, "--status")

    assert "FAILED" not in capsys.readouterr().out


def test_a_role_the_board_still_lists_is_not_closed_when_it_stops_storing(
        tmp_path):
    """`reconcile_board` is fed the RAW ids, not the storable ones. A role
    the board still returns but which newly fails `is_storable` - its
    description was shortened, or it moved abroad - is absent from the
    storable set, and closing it would record "filled" for a role that is
    demonstrably still open."""
    registry(tmp_path)
    good = _ashby_job("good-1", location="Remote", body_len=250)
    still_listed = _ashby_job("thin-1", location="Remote", body_len=250)
    run(tmp_path, fetch=lambda url: {"jobs": [good, still_listed]})

    s = Store(tmp_path / "j.sqlite")
    assert s.get_closed_at("ashby:runway-ml:thin-1") is None

    # Same board, same two roles - but one's description got shortened.
    shortened = _ashby_job("thin-1", location="Remote", body_len=50)
    run(tmp_path, fetch=lambda url: {"jobs": [good, shortened]})

    assert Store(tmp_path / "j.sqlite").get_closed_at(
        "ashby:runway-ml:thin-1") is None


# --- --purge-foreign ------------------------------------------------------

def _insert_stale_foreign_role(db_path, role_id="ashby:runway-ml:stale-1",
                               matched_at=None, role_scored_at=None):
    """A row shaped like one stored before the Part 1 geography fix: its
    location reads `non_us` under the current pattern, but a normal poll
    can never produce this row directly any more, since `is_storable` would
    refuse it. Inserted with raw SQL to simulate what is already on disk."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO postings (id, source, source_thread_id, company,"
        " company_normalised, raw_text, apply_url, is_remote, geo_signal,"
        " posted_at, first_seen, last_seen, status, location, matched_at,"
        " role_scored_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (role_id, "ashby", "runway-ml", "Runway", "runway", "x", None, 0,
         "unknown", "2026-09-01", "2026-09-01", "2026-09-01", "new",
         "London", matched_at, role_scored_at))
    conn.commit()
    conn.close()


def test_purge_foreign_defaults_to_report_only(tmp_path, capsys):
    registry(tmp_path)
    _insert_stale_foreign_role(tmp_path / "j.sqlite")

    run(tmp_path)

    out = capsys.readouterr().out
    assert "foreign" in out.lower()
    assert "1" in out
    assert Store(tmp_path / "j.sqlite").get("ashby:runway-ml:stale-1") is not None


def test_purge_foreign_flag_actually_deletes(tmp_path, capsys):
    registry(tmp_path)
    _insert_stale_foreign_role(tmp_path / "j.sqlite")

    run(tmp_path, "--purge-foreign")

    out = capsys.readouterr().out
    assert "foreign" in out.lower()
    assert Store(tmp_path / "j.sqlite").get("ashby:runway-ml:stale-1") is None


def test_purge_foreign_flag_never_deletes_a_role_with_a_verdict(tmp_path):
    registry(tmp_path)
    _insert_stale_foreign_role(tmp_path / "j.sqlite", matched_at="2026-09-01")

    run(tmp_path, "--purge-foreign")

    assert Store(tmp_path / "j.sqlite").get("ashby:runway-ml:stale-1") is not None


def test_purge_foreign_does_not_run_under_dry_run(tmp_path):
    """--dry-run promises 'fetch and report, store nothing' - purging is a
    delete, which is a mutation, so it must not happen here even with the
    flag also passed."""
    registry(tmp_path)
    _insert_stale_foreign_role(tmp_path / "j.sqlite")

    run(tmp_path, "--dry-run", "--purge-foreign")

    assert Store(tmp_path / "j.sqlite").get("ashby:runway-ml:stale-1") is not None
