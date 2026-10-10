"""Two processes, one database. Until slice 5 there was only ever one.

`Store` held a single connection for the life of the process with no
journal_mode and no busy_timeout. That was documented as deliberate and was
correct for six slices of single-process CLI work. A dashboard reading while
the matching CLI writes is the first time it matters.
"""

import sqlite3

import pytest

from jobhunt.discovery.store import BUSY_TIMEOUT_MS, Store


def test_wal_is_enabled(tmp_path):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    assert s.journal_mode() == "wal"


def test_wal_persists_for_a_second_connection(tmp_path):
    """journal_mode is a property of the FILE, not the connection."""
    Store(tmp_path / "j.sqlite").init_schema()
    assert Store(tmp_path / "j.sqlite").journal_mode() == "wal"


def test_busy_timeout_is_set(tmp_path):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    assert s.busy_timeout() == BUSY_TIMEOUT_MS


def test_a_reader_sees_a_committed_write_from_another_connection(tmp_path):
    """The whole point: the dashboard must see rows the CLI has committed."""
    from jobhunt.discovery.models import Posting
    writer = Store(tmp_path / "j.sqlite")
    writer.init_schema()
    reader = Store(tmp_path / "j.sqlite")
    assert reader.count_postings() == 0

    writer.upsert_posting(Posting(
        id="hn:1", source="hn", source_thread_id="9", company="Acme",
        company_normalised="acme", raw_text="x", apply_url=None,
        is_remote=True, geo_signal="us", posted_at="2026-09-01",
        first_seen="a", last_seen="a", status="new"))

    assert reader.count_postings() == 1


def test_a_read_only_store_refuses_to_write(tmp_path):
    """The dashboard opens read-only so a bug cannot corrupt his data."""
    from jobhunt.discovery.models import Posting
    Store(tmp_path / "j.sqlite").init_schema()
    ro = Store(tmp_path / "j.sqlite", read_only=True)

    assert ro.count_postings() == 0
    with pytest.raises(sqlite3.OperationalError):
        ro.upsert_posting(Posting(
            id="hn:1", source="hn", source_thread_id="9", company="Acme",
            company_normalised="acme", raw_text="x", apply_url=None,
            is_remote=True, geo_signal="us", posted_at="2026-09-01",
            first_seen="a", last_seen="a", status="new"))


def test_a_read_only_store_will_not_create_a_missing_database(tmp_path):
    """Otherwise a typo'd path serves an empty page that looks like 'no
    results' rather than an error."""
    with pytest.raises(sqlite3.OperationalError):
        Store(tmp_path / "absent.sqlite", read_only=True).count_postings()


def test_close_releases_the_connection(tmp_path):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    s.close()
    with pytest.raises(sqlite3.ProgrammingError):
        s.count_postings()


def test_store_works_as_a_context_manager(tmp_path):
    with Store(tmp_path / "j.sqlite") as s:
        s.init_schema()
        assert s.count_postings() == 0
    with pytest.raises(sqlite3.ProgrammingError):
        s.count_postings()


def test_schema_version_is_readable_without_migrating(tmp_path):
    """The dashboard checks the version to decide whether to start, and must
    not migrate as a side effect of looking."""
    Store(tmp_path / "j.sqlite").init_schema()
    ro = Store(tmp_path / "j.sqlite", read_only=True)
    from jobhunt.discovery.store import SCHEMA_VERSION
    assert ro.schema_version() == SCHEMA_VERSION
