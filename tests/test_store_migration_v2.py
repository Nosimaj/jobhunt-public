import sqlite3

from jobhunt.discovery.models import Posting
from jobhunt.discovery.store import SCHEMA_VERSION, Store
from jobhunt.matching.models import MatchVerdict


def posting(pid="hn:1", status="new"):
    return Posting(id=pid, source="hn", source_thread_id="9", company="Acme",
                   company_normalised="acme", raw_text="Acme | Engineer | Remote US",
                   apply_url=None, is_remote=True, geo_signal="us",
                   posted_at="2026-09-01", first_seen="2026-09-20",
                   last_seen="2026-09-20", status=status)


def verdict():
    return MatchVerdict(role_title="Senior Design Engineer", discipline="design",
                        hybrid_signal="explicit", reason="fits", concern="a four-person team may not have a design practice",
                        fit_score=81, bucket="strong")


def test_v1_database_migrates_forward_without_data_loss(tmp_path):
    """The case that matters: the user's populated database is at v1."""
    db = tmp_path / "j.sqlite"
    s = Store(db); s.init_schema()
    s.upsert_posting(posting())
    # Simulate a v1 file by creating a fresh database with only v1 columns.
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE postings")
    conn.execute("""
        CREATE TABLE postings (
          id                  TEXT PRIMARY KEY,
          source              TEXT NOT NULL,
          source_thread_id    TEXT NOT NULL,
          company             TEXT NOT NULL,
          company_normalised  TEXT NOT NULL,
          raw_text            TEXT NOT NULL,
          apply_url           TEXT,
          is_remote           INTEGER NOT NULL,
          geo_signal          TEXT NOT NULL,
          posted_at           TEXT NOT NULL,
          first_seen          TEXT NOT NULL,
          last_seen           TEXT NOT NULL,
          status              TEXT NOT NULL,
          reject_reason       TEXT
        )
    """)
    conn.execute("""
        INSERT INTO postings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("hn:1", "hn", "9", "Acme", "acme", "Acme | Engineer | Remote US", None,
          1, "us", "2026-09-01", "2026-09-20", "2026-09-20", "new", None))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    s2 = Store(db); s2.init_schema()
    assert (sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION)
    assert s2.count_postings() == 1
    assert s2.get("hn:1").company == "Acme"


def test_save_verdict_sets_status_scored_and_all_columns(tmp_path):
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    s.upsert_posting(posting())
    s.save_verdict("hn:1", verdict(), model="claude-opus-5",
                   profile_sha="abc123", matched_at="2026-09-24T10:00:00")
    row = s.get("hn:1")
    assert row.status == "scored"
    stored = s.verdict_of("hn:1")
    assert stored.bucket == "strong" and stored.role_title == "Senior Design Engineer"
    assert stored.concern == "a four-person team may not have a design practice"


def test_unscored_shrinks_as_postings_are_scored(tmp_path):
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    s.upsert_posting(posting("hn:1")); s.upsert_posting(posting("hn:2"))
    s.upsert_posting(posting("hn:3", status="rejected"))
    assert {p.id for p in s.unscored()} == {"hn:1", "hn:2"}
    s.save_verdict("hn:1", verdict(), model="m", profile_sha="a", matched_at="t")
    assert {p.id for p in s.unscored()} == {"hn:2"}


def test_scored_filters_by_bucket(tmp_path):
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    s.upsert_posting(posting("hn:1")); s.upsert_posting(posting("hn:2"))
    s.save_verdict("hn:1", verdict(), model="m", profile_sha="a", matched_at="t")
    s.save_verdict("hn:2", verdict().model_copy(update={"bucket": "no"}),
                   model="m", profile_sha="a", matched_at="t")
    assert [p.id for p in s.scored(bucket="strong")] == ["hn:1"]
    assert len(s.scored()) == 2


def test_stale_verdict_ids_finds_verdicts_scored_against_another_profile(tmp_path):
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    s.upsert_posting(posting("hn:1")); s.upsert_posting(posting("hn:2"))
    s.save_verdict("hn:1", verdict(), model="m", profile_sha="OLD", matched_at="t")
    s.save_verdict("hn:2", verdict(), model="m", profile_sha="NEW", matched_at="t")
    assert s.stale_verdict_ids("NEW") == ["hn:1"]


def test_save_verdict_does_not_clobber_a_downstream_status(tmp_path):
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    s.upsert_posting(posting())
    s.set_status("hn:1", "approved_by_slice_5")
    s.save_verdict("hn:1", verdict(), model="m", profile_sha="a", matched_at="t")
    assert s.get("hn:1").status == "approved_by_slice_5"
