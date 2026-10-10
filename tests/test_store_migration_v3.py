"""v3 repairs a database stamped v2 that never got `runs.kind`.

THE BUG THIS EXISTS FOR, because it will be tempting to "simplify" this away:
`ALTER TABLE runs ADD COLUMN kind` was appended to the v2 migration in b70edf6
*after* v2 had already been applied to the real database an hour earlier in
e6f04d4. A database stamped v2 never replays v2, so it never received the
column - and every matching command, `--status` included, died on
`no such column: kind`.

**Never edit a migration that has already run. Add a version.** v3 is that
version, and it must stay safe to apply to a database that already has the
column, because most databases got it via the edited v2.
"""

import sqlite3

import pytest

from jobhunt.discovery.store import SCHEMA_VERSION, Store

_RUNS_WITHOUT_KIND = """
CREATE TABLE runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  thread_id     TEXT,
  posts_seen    INTEGER NOT NULL DEFAULT 0,
  candidates    INTEGER NOT NULL DEFAULT 0,
  rejected      INTEGER NOT NULL DEFAULT 0,
  new_postings  INTEGER NOT NULL DEFAULT 0,
  error         TEXT
)
"""


def _v2_database_missing_kind(path):
    """Reproduce the real `data/jobs.sqlite`: stamped 2, `runs` has no `kind`,
    and it carries rows that must survive the repair."""
    store = Store(path)
    store.init_schema()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE runs")
    conn.executescript(_RUNS_WITHOUT_KIND)
    conn.execute("INSERT INTO runs (started_at, posts_seen) VALUES ('2026-09-20', 260)")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    return path


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_a_v2_database_missing_kind_gains_the_column(tmp_path):
    db = _v2_database_missing_kind(tmp_path / "j.sqlite")
    assert "kind" not in _columns(db, "runs")      # precondition

    Store(db).init_schema()

    assert "kind" in _columns(db, "runs")


def test_the_repair_stamps_the_current_version(tmp_path):
    db = _v2_database_missing_kind(tmp_path / "j.sqlite")

    store = Store(db)
    store.init_schema()

    assert store.schema_version() == SCHEMA_VERSION


def test_the_repair_preserves_existing_run_rows(tmp_path):
    db = _v2_database_missing_kind(tmp_path / "j.sqlite")

    Store(db).init_schema()

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT posts_seen, kind FROM runs").fetchone()
    conn.close()
    assert row[0] == 260
    # Backfilled by the column default, so pre-existing rows read as the
    # discovery polls they actually were - not as matching runs.
    assert row[1] == "discovery"


def test_last_run_works_after_the_repair(tmp_path):
    """The actual symptom: `--status` called `last_run`, which filters on
    `kind`, and took every matching command down with it."""
    db = _v2_database_missing_kind(tmp_path / "j.sqlite")
    store = Store(db)
    store.init_schema()

    assert store.last_run(Store.DISCOVERY) is not None
    assert store.last_run(Store.MATCH) is None


def test_v3_is_safe_on_a_database_that_already_has_kind(tmp_path):
    """Most databases received `kind` from the edited v2. Re-issuing the same
    ALTER must not raise `duplicate column name`."""
    db = tmp_path / "j.sqlite"
    store = Store(db)
    store.init_schema()
    assert "kind" in _columns(db, "runs")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 2")     # has the column, stamped older
    conn.commit()
    conn.close()

    Store(db).init_schema()                      # must not raise

    assert Store(db).schema_version() == SCHEMA_VERSION


def test_a_fresh_database_is_current_and_has_kind(tmp_path):
    db = tmp_path / "fresh.sqlite"
    store = Store(db)
    store.init_schema()

    assert store.schema_version() == SCHEMA_VERSION
    assert "kind" in _columns(db, "runs")


def test_init_schema_is_idempotent_across_repeated_opens(tmp_path):
    db = _v2_database_missing_kind(tmp_path / "j.sqlite")
    for _ in range(3):
        Store(db).init_schema()
    assert Store(db).schema_version() == SCHEMA_VERSION
