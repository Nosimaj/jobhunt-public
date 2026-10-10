"""Storing the reach decision. Like the prefilter, it must be auditable and
NEVER permanent: widening the home area and re-running must un-exclude."""
import pytest

from jobhunt.discovery.models import Posting
from jobhunt.discovery.store import SCHEMA_VERSION, Store


def posting(pid):
    return Posting(id=pid, source="ashby", source_thread_id="t",
                   company="Acme", company_normalised="acme",
                   raw_text="x" * 300, apply_url=None, is_remote=True,
                   geo_signal="us", posted_at="2026-09-01", first_seen="a",
                   last_seen="a", status="new")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    for pid in ("a:1", "a:2", "a:3"):
        s.upsert_posting(posting(pid))
    s._conn.execute("UPDATE postings SET role_title='Product Designer'")
    s._conn.commit()
    return s


def test_schema_version_is_eight():
    assert SCHEMA_VERSION == 8


def test_apply_reachability_stores_and_counts_out_of_area(store):
    n = store.apply_reachability({"a:1": "remote", "a:2": "out_of_area",
                                  "a:3": "local"})
    assert n == 1
    assert store.reach_counts() == {"remote": 1, "out_of_area": 1, "local": 1}


def test_out_of_area_rows_are_excluded_from_scoring(store):
    store.apply_reachability({"a:1": "remote", "a:2": "out_of_area",
                              "a:3": "local"})
    ids = {p.id for p in store.postings_needing_role_score("sha")}
    assert ids == {"a:1", "a:3"}


def test_unknown_is_kept_not_excluded(store):
    """REVIEW FOCUS 1. Most of the live corpus has no usable location."""
    store.apply_reachability({"a:1": "unknown", "a:2": "unknown",
                              "a:3": "unknown"})
    assert len(store.postings_needing_role_score("sha")) == 3


def test_a_null_reach_is_still_scorable(store):
    """Every pre-existing row has NULL reach until the first audit runs."""
    assert len(store.postings_needing_role_score("sha")) == 3


def test_a_changed_value_un_excludes(store):
    store.apply_reachability({"a:1": "out_of_area"})
    assert "a:1" not in {p.id for p in store.postings_needing_role_score("sha")}

    store.apply_reachability({"a:1": "local"})
    assert "a:1" in {p.id for p in store.postings_needing_role_score("sha")}


def test_an_excluded_row_is_still_offered_for_re_audit(store):
    """The C1 lesson, applied up front: the audit source must INCLUDE rows it
    has already excluded, or widening the home area is a silent no-op."""
    store.apply_reachability({"a:1": "out_of_area"})
    assert "a:1" in {p.id for p in store.postings_eligible_for_prefilter()}


def test_reach_does_not_disturb_the_prefilter_column(store):
    store.apply_prefilter({"a:1": "sales: no signal"})
    store.apply_reachability({"a:1": "local"})
    assert dict((i, r) for i, _t, r in store.dropped_postings())["a:1"]


def test_role_locations_reads_columns_the_model_does_not_carry(store):
    """`Posting` has neither field; a caller reaching for `p.location` gets an
    AttributeError, which is how `p.role_title` failed once already."""
    assert "location" not in Posting.model_fields
    store._conn.execute(
        "UPDATE postings SET location='Boston, MA', workplace_type='onsite'"
        " WHERE id='a:1'")
    store._conn.commit()
    assert store.role_locations(["a:1"])["a:1"] == ("Boston, MA", "onsite")
    assert store.role_locations([]) == {}


def test_roles_by_reach_lists_title_and_location(store):
    store._conn.execute("UPDATE postings SET location='Austin, TX' WHERE id='a:1'")
    store._conn.commit()
    store.apply_reachability({"a:1": "out_of_area"})
    assert store.roles_by_reach("out_of_area") == [
        ("a:1", "Product Designer", "Austin, TX")]
