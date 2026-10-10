"""A role that vanishes from a board has been filled or withdrawn.

Slice 2's spec states closure "is NOT knowable from an immutable thread", so
HN rows fall back to a 45-day age heuristic. An ATS board is not immutable, so
for these rows closure is a fact rather than a guess.
"""

import pytest

from jobhunt.discovery.models import AtsRole
from jobhunt.discovery.store import Store


def role(rid, title="Design Engineer"):
    return AtsRole(id=rid, provider="ashby", token="runway-ml", company="Runway",
                   title=title, body="duties " * 60, location="Remote",
                   workplace_type="remote", department="Design",
                   apply_url=None, posted_at="2026-09-20")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    for n in (1, 2, 3):
        s.upsert_ats_role(role(f"ashby:runway-ml:{n}"), now="2026-09-29")
    return s


def test_a_role_missing_from_the_board_is_closed(store):
    store.reconcile_board("ashby", "runway-ml",
                          {"ashby:runway-ml:1", "ashby:runway-ml:2"},
                          now="2026-09-30")
    assert store.get_closed_at("ashby:runway-ml:3") == "2026-09-30"


def test_roles_still_on_the_board_stay_open(store):
    store.reconcile_board("ashby", "runway-ml",
                          {"ashby:runway-ml:1", "ashby:runway-ml:2"},
                          now="2026-09-30")
    assert store.get_closed_at("ashby:runway-ml:1") is None


def test_an_empty_board_closes_nothing(store):
    """REVIEW FOCUS 1, and the single most dangerous case in this slice. A
    board that returns zero roles - an outage, a moved token, a rename -
    must NEVER be read as 'every role closed'. That would wipe the pipeline
    silently and look like a successful run."""
    closed = store.reconcile_board("ashby", "runway-ml", set(), now="2026-09-30")
    assert closed == 0
    assert all(store.get_closed_at(f"ashby:runway-ml:{n}") is None
               for n in (1, 2, 3))


def test_reconciling_never_touches_another_companys_roles(store):
    store.upsert_ats_role(
        role("greenhouse:oklo:1").model_copy(
            update={"provider": "greenhouse", "token": "oklo",
                    "company": "Oklo"}), now="2026-09-29")
    store.reconcile_board("ashby", "runway-ml", {"ashby:runway-ml:1"},
                          now="2026-09-30")
    assert store.get_closed_at("greenhouse:oklo:1") is None


def test_reconciling_never_touches_another_board_on_the_same_provider(store):
    """The realistic collision: two companies on the SAME provider (ashby is
    32 of the 42 boards in the real registry), distinguished only by token.
    A WHERE clause that filters on provider alone would close one company's
    roles when another company's board on that provider was polled."""
    store.upsert_ats_role(
        role("ashby:beta-inc:1").model_copy(
            update={"token": "beta-inc", "company": "Beta Inc"}),
        now="2026-09-29")
    store.reconcile_board("ashby", "runway-ml", {"ashby:runway-ml:1"},
                          now="2026-09-30")
    assert store.get_closed_at("ashby:beta-inc:1") is None


def test_reconciling_never_touches_hn_rows(store):
    from jobhunt.discovery.models import Posting
    store.upsert_posting(Posting(
        id="hn:1", source="hn", source_thread_id="9", company="Odin",
        company_normalised="odin", raw_text="x", apply_url=None, is_remote=True,
        geo_signal="us", posted_at="2026-09-01", first_seen="a", last_seen="a",
        status="new"))
    store.reconcile_board("ashby", "runway-ml", {"ashby:runway-ml:1"},
                          now="2026-09-30")
    assert store.get_closed_at("hn:1") is None


def test_closing_twice_does_not_move_the_date(store):
    """The closure date is when it was FIRST seen gone."""
    store.reconcile_board("ashby", "runway-ml", {"ashby:runway-ml:1"},
                          now="2026-09-30")
    store.reconcile_board("ashby", "runway-ml", {"ashby:runway-ml:1"},
                          now="2026-10-05")
    assert store.get_closed_at("ashby:runway-ml:3") == "2026-09-30"
