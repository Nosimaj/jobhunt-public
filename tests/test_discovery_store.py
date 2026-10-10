import pytest

from jobhunt.discovery.models import Posting
from jobhunt.discovery.store import (_MIGRATIONS, SCHEMA_VERSION,
                                     STALE_DAYS, Store)


def posting(pid="hn:1", status="new", company="Acme", posted="2026-09-01",
            seen="2026-09-20", reason=None, text="Acme | Engineer | Remote US"):
    return Posting(id=pid, source="hn", source_thread_id="49522897",
                   company=company, company_normalised=company.casefold(),
                   raw_text=text, apply_url=None, is_remote=True,
                   geo_signal="us", posted_at=posted, first_seen=seen,
                   last_seen=seen, status=status, reject_reason=reason)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "jobs.sqlite")
    s.init_schema()
    return s


def test_init_schema_is_idempotent(tmp_path):
    s = Store(tmp_path / "jobs.sqlite")
    s.init_schema()
    s.init_schema()
    assert s.count_postings() == 0
    assert s.schema_version() == SCHEMA_VERSION


def test_a_fresh_database_lands_at_the_current_schema_version(tmp_path):
    s = Store(tmp_path / "jobs.sqlite")
    assert s.schema_version() == 0, "a brand-new sqlite file is stamped 0"
    s.init_schema()
    assert s.schema_version() == SCHEMA_VERSION


def test_migrations_are_ordered_and_reach_the_declared_version():
    """Guard against appending a migration and forgetting to bump
    SCHEMA_VERSION - which would leave every database stamped short and the
    migration replayed on every run."""
    versions = [v for v, _ in _MIGRATIONS]
    assert versions == sorted(set(versions)), "must be strictly increasing"
    assert versions[0] == 1
    assert versions[-1] == SCHEMA_VERSION


def test_a_database_stamped_at_zero_is_migrated_forward_without_data_loss(tmp_path):
    """The live database has PRAGMA user_version = 0 and holds real rows.

    CREATE TABLE IF NOT EXISTS is not a migration path, so slice 3 had
    nowhere to put the normalised role title spec section 8 requires: adding
    a column to _SCHEMA is a no-op against an existing database, and every
    read then fails with `no such column` once _COLUMNS grows.
    """
    import sqlite3
    db = tmp_path / "legacy.sqlite"
    legacy = sqlite3.connect(db)
    legacy.executescript("""
        CREATE TABLE postings (
          id TEXT PRIMARY KEY, source TEXT NOT NULL,
          source_thread_id TEXT NOT NULL, company TEXT NOT NULL,
          company_normalised TEXT NOT NULL, raw_text TEXT NOT NULL,
          apply_url TEXT, is_remote INTEGER NOT NULL,
          geo_signal TEXT NOT NULL, posted_at TEXT NOT NULL,
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
          status TEXT NOT NULL, reject_reason TEXT);
        CREATE TABLE runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
          finished_at TEXT, thread_id TEXT,
          posts_seen INTEGER NOT NULL DEFAULT 0,
          candidates INTEGER NOT NULL DEFAULT 0,
          rejected INTEGER NOT NULL DEFAULT 0,
          new_postings INTEGER NOT NULL DEFAULT 0, error TEXT);
        PRAGMA user_version = 0;
    """)
    legacy.commit()
    legacy.close()

    pre = Store(db)
    assert pre.schema_version() == 0
    pre.upsert_posting(posting(pid="hn:legacy", company="Legacy Co"))
    # Written in the LEGACY column shape, not through start_run/finish_run:
    # the v1 `runs` table has no `kind` column, so the current writers cannot
    # target it. Every entry point calls init_schema() before writing, so
    # this is only how a legacy row got there in the first place.
    legacy = sqlite3.connect(db)
    legacy.execute(
        "INSERT INTO runs (started_at, finished_at, thread_id, posts_seen,"
        " candidates, rejected, new_postings) VALUES (?,?,?,?,?,?,?)",
        ("2026-08-01T10:00:00", "2026-08-01T10:00:05", "49156683", 1, 1, 0, 1))
    legacy.commit()
    legacy.close()

    migrated = Store(db)
    migrated.init_schema()
    assert migrated.schema_version() == SCHEMA_VERSION
    # No data loss.
    assert migrated.count_postings() == 1
    assert migrated.get("hn:legacy").company == "Legacy Co"
    assert migrated.last_successful_run().thread_id == "49156683"
    # And it is still usable afterwards.
    assert migrated.upsert_posting(posting(pid="hn:2")) is True


def test_migrating_an_already_current_database_changes_nothing(tmp_path):
    db = tmp_path / "jobs.sqlite"
    s = Store(db)
    s.init_schema()
    s.upsert_posting(posting())
    Store(db).init_schema()
    again = Store(db)
    assert again.schema_version() == SCHEMA_VERSION
    assert again.count_postings() == 1


def test_insert_then_upsert_does_not_duplicate(store):
    assert store.upsert_posting(posting()) is True
    assert store.upsert_posting(posting(seen="2026-09-27")) is False
    assert store.count_postings() == 1


def test_upsert_updates_last_seen_but_not_first_seen(store):
    store.upsert_posting(posting(seen="2026-09-20"))
    store.upsert_posting(posting(seen="2026-09-27"))
    row = store.get("hn:1")
    assert row.first_seen == "2026-09-20"
    assert row.last_seen == "2026-09-27"


def test_upsert_never_overwrites_a_downstream_status(store):
    """Slices 3-5 own the status vocabulary. Re-polling must not reset it."""
    store.upsert_posting(posting(status="new"))
    store.set_status("hn:1", "approved_by_slice_5")
    store.upsert_posting(posting(status="new", seen="2026-09-27"))
    row = store.get("hn:1")
    assert row.status == "approved_by_slice_5"
    assert row.last_seen == "2026-09-27"


def test_upsert_reclassifies_a_rejection_that_became_a_candidate(store):
    """Slice 2 owns `rejected`, so it must re-evaluate it.

    A post first seen as "Onsite NYC" is stored rejected/not_remote. Edited
    to "Remote US" and re-polled, the row must become a candidate with the
    reason cleared - otherwise its stated reason contradicts its own
    is_remote and geo_signal columns and candidates() can never see it.
    This is also the only path by which a CORRECTED FILTER reaches rows that
    are already stored.
    """
    store.upsert_posting(posting(status="rejected", reason="not_remote",
                                 text="Acme | Engineer | Onsite NYC"))
    store.upsert_posting(posting(status="new", reason=None, seen="2026-09-27",
                                 text="Acme | Engineer | Remote US"))
    row = store.get("hn:1")
    assert row.status == "new"
    assert row.reject_reason is None
    assert row.last_seen == "2026-09-27"
    assert [r.id for r in store.candidates()] == ["hn:1"]
    assert store.rejections() == []


def test_upsert_records_a_candidate_that_became_a_rejection(store):
    store.upsert_posting(posting(status="new", reason=None))
    store.upsert_posting(posting(status="rejected", reason="eu_only",
                                 seen="2026-09-27"))
    row = store.get("hn:1")
    assert row.status == "rejected"
    assert row.reject_reason == "eu_only"
    assert [r.id for r in store.rejections()] == ["hn:1"]
    assert store.candidates() == []


def test_upsert_leaves_a_downstream_reject_reason_alone(store):
    """The status guard covers reject_reason too: slice 2 must not stamp a
    reason onto a row whose status it no longer owns."""
    store.upsert_posting(posting(status="new"))
    store.set_status("hn:1", "applied_by_slice_5")
    store.upsert_posting(posting(status="rejected", reason="eu_only",
                                 seen="2026-09-27"))
    row = store.get("hn:1")
    assert row.status == "applied_by_slice_5"
    assert row.reject_reason is None
    assert row.last_seen == "2026-09-27"


def test_upsert_refreshes_the_mutable_content_fields(store):
    store.upsert_posting(posting(text="Acme | Engineer | Remote US"))
    store.upsert_posting(posting(text="Acme | Staff Engineer | Remote US"))
    assert "Staff" in store.get("hn:1").raw_text


def test_times_seen_company_counts_prior_postings(store):
    store.upsert_posting(posting(pid="hn:1", company="Acme", posted="2026-07-01"))
    store.upsert_posting(posting(pid="hn:2", company="Acme", posted="2026-08-01"))
    store.upsert_posting(posting(pid="hn:3", company="Other", posted="2026-08-01"))
    assert store.times_seen_company("acme") == 2
    assert store.times_seen_company("other") == 1
    assert store.times_seen_company("nobody") == 0


def test_rejections_are_retrievable_with_their_reason(store):
    store.upsert_posting(posting(pid="hn:1", status="new"))
    store.upsert_posting(posting(pid="hn:2", status="rejected", reason="eu_only"))
    rows = store.rejections()
    assert [r.id for r in rows] == ["hn:2"]
    assert rows[0].reject_reason == "eu_only"


def test_stale_postings_use_the_45_day_window(store):
    store.upsert_posting(posting(pid="hn:old", posted="2026-07-01"))
    store.upsert_posting(posting(pid="hn:new", posted="2026-09-01"))
    stale = store.stale_postings(as_of="2026-09-20")
    assert [r.id for r in stale] == ["hn:old"]


def test_a_posting_exactly_stale_days_old_is_not_yet_stale(store):
    """Boundary. as_of 2026-09-20 with STALE_DAYS=45 gives a cutoff of
    2026-08-06, and the comparison is `posted_at < cutoff` - so a posting
    dated exactly on the cutoff is NOT stale.

    Pins two mutations that both passed all 194 tests: STALE_DAYS 45->30,
    and `posted_at < ?` -> `<=`.
    """
    assert STALE_DAYS == 45
    store.upsert_posting(posting(pid="hn:exactly45", posted="2026-08-06"))
    assert store.stale_postings(as_of="2026-09-20") == []


def test_a_posting_one_day_past_stale_days_is_stale(store):
    store.upsert_posting(posting(pid="hn:exactly46", posted="2026-08-05"))
    assert [r.id for r in store.stale_postings(as_of="2026-09-20")] == ["hn:exactly46"]


def test_the_stale_boundary_separates_adjacent_days(store):
    """Both sides of the boundary in one table, so an off-by-one in either
    direction shows up as a changed list rather than an empty one."""
    store.upsert_posting(posting(pid="hn:d44", posted="2026-08-07"))
    store.upsert_posting(posting(pid="hn:d45", posted="2026-08-06"))
    store.upsert_posting(posting(pid="hn:d46", posted="2026-08-05"))
    assert [r.id for r in store.stale_postings(as_of="2026-09-20")] == ["hn:d46"]


def test_run_is_recorded_on_success(store):
    rid = store.start_run("2026-09-20T10:00:00")
    store.finish_run(rid, finished_at="2026-09-20T10:00:05", thread_id="49522897",
                     posts_seen=260, candidates=136, rejected=124, new_postings=136)
    run = store.last_run()
    assert run.thread_id == "49522897" and run.candidates == 136
    assert run.error is None and run.finished_at == "2026-09-20T10:00:05"


def test_run_is_recorded_on_failure(store):
    rid = store.start_run("2026-09-20T10:00:00")
    store.finish_run(rid, finished_at="2026-09-20T10:00:01", error="boom")
    run = store.last_run()
    assert run.error == "boom"
    assert run.candidates == 0


def test_last_run_is_none_on_a_fresh_database(store):
    assert store.last_run() is None


def test_last_successful_run_skips_a_failed_run_that_came_after_it(store):
    """last_run() and last_successful_run() must diverge once a run fails.

    Record a SUCCESSFUL run, then a FAILED one on top of it. last_run()
    must report the failed run (it is the most recent by id); last_successful_run()
    must skip over it and report the earlier successful one.
    """
    ok_id = store.start_run("2026-09-20T10:00:00")
    store.finish_run(ok_id, finished_at="2026-09-20T10:00:05", thread_id="49522897",
                     posts_seen=260, candidates=136, rejected=124, new_postings=260)

    bad_id = store.start_run("2026-10-20T10:00:00")
    store.finish_run(bad_id, finished_at="2026-10-20T10:00:01", error="boom")

    last = store.last_run()
    last_ok = store.last_successful_run()
    assert last.id != last_ok.id
    assert last.error == "boom"
    assert last_ok.error is None
    assert last_ok.thread_id == "49522897"


def test_last_successful_run_is_none_when_the_only_run_failed(store):
    rid = store.start_run("2026-09-20T10:00:00")
    store.finish_run(rid, finished_at="2026-09-20T10:00:01", error="boom")

    assert store.last_run() is not None
    assert store.last_run().error == "boom"
    assert store.last_successful_run() is None


def test_a_matching_run_is_not_visible_to_discoverys_run_queries(store):
    """The heartbeat regression. `runs` is shared by slice 2 and slice 3, and
    with no `kind` column a matching run looked like a SUCCESSFUL discovery
    poll: it silenced both "last successful run was N days ago" and "the most
    recent run FAILED", so a total discovery outage read as healthy.
    """
    ok = store.start_run("2026-03-01T09:00:00")
    store.finish_run(ok, finished_at="2026-03-01T09:01:00", thread_id="49156683",
                     posts_seen=260, candidates=132, rejected=128, new_postings=260)
    bad = store.start_run("2026-09-23T09:00:00")
    store.finish_run(bad, finished_at="2026-09-23T09:00:01", error="APIConnectionError")

    match_id = store.start_run("2026-09-24T10:00:00", Store.MATCH)
    store.finish_run(match_id, finished_at="2026-09-24T10:05:00")

    # Discovery still sees its own failure and its own last success.
    assert store.last_run().error == "APIConnectionError"
    assert store.last_successful_run().thread_id == "49156683"
    assert store.failed_runs_since(ok) == 1
    # And matching can still find its own row.
    assert store.last_run(Store.MATCH).id == match_id
    assert store.failed_runs_since(kind=Store.MATCH) == 0


def test_a_half_applied_migration_can_be_replayed(tmp_path):
    """`executescript` issues an implicit COMMIT and is not atomic, and
    `user_version` is stamped only after the whole script - so an interruption
    part-way through leaves SOME columns added at the OLD version. Replaying
    used to die on `duplicate column name` forever, and since init_schema() is
    the first thing both CLIs do, that took down every command including
    --status.
    """
    import sqlite3
    db = tmp_path / "half.sqlite"
    s = Store(db); s.init_schema()
    s.upsert_posting(posting(pid="hn:1", company="Acme"))

    conn = sqlite3.connect(db)
    conn.executescript("PRAGMA user_version = 1;")   # pretend v2 never finished
    conn.commit(); conn.close()

    again = Store(db)
    again.init_schema()                               # must not raise
    assert again.schema_version() == SCHEMA_VERSION
    assert again.count_postings() == 1
    Store(db).init_schema()                           # still idempotent


def test_candidates_includes_a_scored_posting(store):
    """Pinned deliberately: `candidates()` is slice 2's 132 and must not
    shrink as slice 3 scores them. A future slice "fixing" this to mean
    'unprocessed' would silently move a documented measured fact - use
    `unscored()` for that.
    """
    store.upsert_posting(posting(pid="hn:1", company="Acme"))
    assert [p.id for p in store.candidates()] == ["hn:1"]
    store.set_status("hn:1", "scored")
    assert [p.id for p in store.candidates()] == ["hn:1"]
    assert store.unscored() == []
