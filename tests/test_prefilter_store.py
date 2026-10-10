"""Storing prefilter decisions. A drop must never be silent or permanent."""

import pytest

from jobhunt.discovery.models import Posting
from jobhunt.discovery.store import SCHEMA_VERSION, Store


def posting(pid, title):
    return Posting(id=pid, source="ashby", source_thread_id="t",
                   company="Acme", company_normalised="acme",
                   raw_text="x" * 300, apply_url=None, is_remote=True,
                   geo_signal="us", posted_at="2026-09-01", first_seen="a",
                   last_seen="a", status="new")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    for pid, t in (("a:1", "Account Executive"), ("a:2", "Product Designer")):
        p = posting(pid, t)
        s.upsert_posting(p)
        s._conn.execute("UPDATE postings SET role_title=? WHERE id=?", (t, pid))
    s._conn.commit()
    return s


def test_schema_version_is_eight(store):
    assert SCHEMA_VERSION == 8
    assert store.schema_version() == 8


def test_a_drop_reason_is_stored(store):
    store.apply_prefilter({"a:1": "account executive: no signal", "a:2": None})
    dropped = {d[0] for d in store.dropped_postings()}
    assert dropped == {"a:1"}


def test_a_dropped_role_is_not_deleted(store):
    store.apply_prefilter({"a:1": "account executive: no signal"})
    assert store.get("a:1") is not None


def test_a_dropped_role_keeps_its_status(store):
    """Slice 2 owns `status`. The prefilter must not touch it."""
    store.apply_prefilter({"a:1": "account executive: no signal"})
    assert store.get("a:1").status == "new"


def test_a_dropped_role_is_excluded_from_scoring(store):
    store.apply_prefilter({"a:1": "account executive: no signal"})
    assert [p.id for p in store.postings_needing_role_score("sha")] == ["a:2"]


def test_loosening_the_filter_undrops(store):
    """REVIEW FOCUS 3. No stale exclusions - re-running recomputes."""
    store.apply_prefilter({"a:1": "account executive: no signal"})
    assert store.count_prefiltered() == 1

    store.apply_prefilter({"a:1": None})

    assert store.count_prefiltered() == 0
    assert "a:1" in [p.id for p in store.postings_needing_role_score("sha")]


def test_the_reason_comes_back_with_the_title(store):
    store.apply_prefilter({"a:1": "account executive: no signal"})
    pid, title, reason = store.dropped_postings()[0]
    assert title == "Account Executive" and "account executive" in reason


def test_apply_prefilter_returns_the_drop_count(store):
    assert store.apply_prefilter({"a:1": "x", "a:2": None}) == 1


def test_a_closed_role_is_not_reported_as_prefiltered(store):
    """A closed requisition is not something we are choosing to skip, and
    `action_counts()` right next to these already filters closed rows. The
    dashboard chip read 350 against an open-role figure of 345."""
    store.apply_prefilter({"a:1": "sales: no signal", "a:2": "sales: no signal"})
    store._conn.execute("UPDATE postings SET closed_at='2026-10-01' WHERE id='a:2'")
    store._conn.commit()

    assert store.count_prefiltered() == 1
    assert [r[0] for r in store.dropped_postings()] == ["a:1"]
