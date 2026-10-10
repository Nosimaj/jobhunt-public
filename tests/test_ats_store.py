"""Persisting ATS roles into the shared postings table."""

import pytest

from jobhunt.discovery.models import AtsRole
from jobhunt.discovery.store import Store


def role(rid="ashby:runway-ml:42", title="Design Engineer", body=None):
    return AtsRole(id=rid, provider="ashby", token="runway-ml", company="Runway",
                   title=title, body=body or ("duties " * 60),
                   location="Remote", workplace_type="remote",
                   department="Design", apply_url="https://x/y",
                   posted_at="2026-09-20")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    return s


def test_a_role_is_stored_as_a_posting(store):
    assert store.upsert_ats_role(role(), now="2026-09-29") is True
    p = store.get("ashby:runway-ml:42")
    assert p.company == "Runway"
    assert p.source == "ashby"


def test_the_title_and_body_both_reach_raw_text(store):
    """Rubric A gets ONE text field. A body without its title loses the role
    name; a title without the body loses the duties."""
    store.upsert_ats_role(role(title="Staff Product Designer"), now="2026-09-29")
    raw = store.get("ashby:runway-ml:42").raw_text
    assert "Staff Product Designer" in raw and "duties" in raw


def test_re_upserting_is_not_a_new_row(store):
    store.upsert_ats_role(role(), now="2026-09-29")
    assert store.upsert_ats_role(role(), now="2026-09-30") is False
    assert store.count_postings() == 1


def test_re_upserting_refreshes_last_seen_but_not_first_seen(store):
    store.upsert_ats_role(role(), now="2026-09-29")
    store.upsert_ats_role(role(), now="2026-09-30")
    p = store.get("ashby:runway-ml:42")
    assert p.first_seen == "2026-09-29" and p.last_seen == "2026-09-30"


def test_ids_from_two_providers_do_not_collide(store):
    """Review Focus 3. Greenhouse and Lever both use bare integers."""
    store.upsert_ats_role(role(rid="greenhouse:oklo:4001"), now="2026-09-29")
    store.upsert_ats_role(role(rid="lever:findigs:4001"), now="2026-09-29")
    assert store.count_postings() == 2


def test_an_ats_role_does_not_disturb_the_hn_rows(store):
    from jobhunt.discovery.models import Posting
    store.upsert_posting(Posting(
        id="hn:1", source="hn", source_thread_id="9", company="Odin",
        company_normalised="odin", raw_text="x", apply_url=None, is_remote=True,
        geo_signal="us", posted_at="2026-09-01", first_seen="a", last_seen="a",
        status="new"))
    store.upsert_ats_role(role(), now="2026-09-29")
    assert store.get("hn:1").status == "new"
    assert store.count_postings() == 2


def test_re_upserting_refreshes_geo_signal_and_is_remote(store):
    """A re-poll must actually correct a stale classification. Before this,
    the UPDATE branch refreshed `location` but not `geo_signal` or
    `is_remote`, so a role stored `unknown` (or wrongly `non_us`-adjacent)
    under the old geography pattern would stay that way forever even after
    the pattern was fixed and the board was polled again - both are
    re-derived from board data every poll, so a stale value here is simply
    wrong."""
    onsite_unknown = role().model_copy(
        update={"location": "Somewhere Obscure", "workplace_type": "onsite"})
    store.upsert_ats_role(onsite_unknown, now="2026-09-29")
    p = store.get("ashby:runway-ml:42")
    assert p.geo_signal == "unknown"
    assert p.is_remote is False

    remote_us = role().model_copy(
        update={"location": "Long Beach, California", "workplace_type": "remote"})
    store.upsert_ats_role(remote_us, now="2026-09-30")
    p2 = store.get("ashby:runway-ml:42")
    assert p2.geo_signal == "us"
    assert p2.is_remote is True


def test_reopening_a_closed_role_clears_closed_at(store):
    store.upsert_ats_role(role(), now="2026-09-29")
    store.mark_closed(["ashby:runway-ml:42"], now="2026-09-30")
    store.upsert_ats_role(role(), now="2026-10-01")
    assert store.get_closed_at("ashby:runway-ml:42") is None


def test_a_foreign_role_is_refused_not_silently_dropped(store):
    """upsert_ats_role must not trust its caller: nothing else enforces that
    is_storable ran first. A False return would be indistinguishable from
    'stored but not new' and would corrupt a caller's new-row count."""
    foreign = role().model_copy(update={"location": "London"})
    with pytest.raises(ValueError, match="ashby:runway-ml:42"):
        store.upsert_ats_role(foreign, now="2026-09-29")
    assert store.count_postings() == 0


def test_a_thin_role_is_refused_not_silently_dropped(store):
    thin = role(body="apply here")
    with pytest.raises(ValueError, match="ashby:runway-ml:42"):
        store.upsert_ats_role(thin, now="2026-09-29")
    assert store.count_postings() == 0


# --- where the new WRITERS meet the older READERS -------------------------

def hn_posting(pid="hn:1", posted_at="2026-09-01", status="new"):
    from jobhunt.discovery.models import Posting
    return Posting(id=pid, source="hn", source_thread_id="9", company="Odin",
                   company_normalised="odin", raw_text="Odin | Design Engineer",
                   apply_url=None, is_remote=True, geo_signal="us",
                   posted_at=posted_at, first_seen="a", last_seen="a",
                   status=status)


def test_an_ats_role_is_never_returned_by_unscored(store):
    """`unscored()` feeds the RETIRED single-axis scorer, and the bare
    `python -m jobhunt.matching` is documented as 'score every unscored
    posting'. ATS rows are inserted status='new' and the two-axis path
    deliberately never writes `status`, so without a source filter that
    command would score ~1,039 ATS roles - ~$29 - against the very rubric
    this slice exists to retire."""
    store.upsert_posting(hn_posting())
    store.upsert_ats_role(role(), now="2026-09-29")

    assert [p.id for p in store.unscored()] == ["hn:1"]


def test_a_months_old_ats_role_is_not_reported_stale(store):
    """Spec section 6: the 45-day age heuristic "stays for HN rows". ATS
    posted_at comes from Greenhouse's updated_at / Ashby's publishedAt, which
    are routinely months old on roles that are demonstrably still open -
    closure is known for real from the board, so age says nothing here."""
    store.upsert_posting(hn_posting("hn:old", posted_at="2026-01-01"))
    store.upsert_ats_role(role().model_copy(update={"posted_at": "2026-01-01"}),
                          now="2026-09-29")

    assert [p.id for p in store.stale_postings(as_of="2026-09-29")] == ["hn:old"]


def test_a_closed_role_is_not_a_candidate(store):
    store.upsert_ats_role(role(), now="2026-09-29")
    store.mark_closed(["ashby:runway-ml:42"], now="2026-09-30")

    assert store.candidates() == []


def test_a_closed_role_is_not_unscored(store):
    """A filled role must never be re-scored - that is spend on a job nobody
    can apply for.

    Deliberately exercised on an HN row. `unscored()` also filters
    `source = 'hn'`, so closing an ATS role would pass this whether or not
    the `closed_at` clause were there, and the test would be measuring the
    wrong guard. `mark_closed` is public and takes any id.
    """
    store.upsert_posting(hn_posting("hn:filled"))
    assert [p.id for p in store.unscored()] == ["hn:filled"]

    store.mark_closed(["hn:filled"], now="2026-09-30")

    assert store.unscored() == []


def test_a_closed_role_does_not_need_a_role_score(store):
    store.upsert_ats_role(role(), now="2026-09-29")
    assert [p.id for p in store.postings_needing_role_score("s")] \
        == ["ashby:runway-ml:42"]

    store.mark_closed(["ashby:runway-ml:42"], now="2026-09-30")

    assert store.postings_needing_role_score("s") == []


def test_a_closed_role_is_not_shown_by_action(store):
    """`--show-actions` recommending an apply for a filled role is the
    user-visible face of this bug."""
    from jobhunt.matching.models import CompanyVerdict, RoleVerdict
    store.upsert_ats_role(role(), now="2026-09-29")
    store.save_role_verdict(
        "ashby:runway-ml:42",
        RoleVerdict(role_title="Design Engineer", discipline="design",
                    reason="right discipline", hybrid_test="n/a", domain_fit="none",
                    hybrid_evidence=[], overlaps=["x"], gaps=[],
                    role_fit="strong"),
        model="m", prompt_sha="s")
    store.save_company_verdict(
        "runway", "Runway",
        CompanyVerdict(reason="r", domain_pull="present",
                       designs_how_things_work="present",
                       design_valued="present", stage_fit="unknown",
                       reachability="unknown", disqualifier=None,
                       company_fit="target"),
        model="m", prompt_sha="s")
    store.refresh_actions()
    assert [p.id for p in store.by_action("apply_priority")] \
        == ["ashby:runway-ml:42"]

    store.mark_closed(["ashby:runway-ml:42"], now="2026-09-30")

    assert store.by_action("apply_priority") == []


def test_a_closed_role_does_not_appear_in_action_counts(store):
    """`action_counts()` backs `--status`'s action summary; `by_action()`
    backs `--show-actions`. Before this fix a closed role vanished from
    `by_action()` but not `action_counts()`, so `python -m jobhunt.matching
    --status` could report `apply_priority=1` while `--show-actions` showed
    nothing for it - the same disagreement `by_action` was already fixed for."""
    from jobhunt.matching.models import CompanyVerdict, RoleVerdict
    store.upsert_ats_role(role(), now="2026-09-29")
    store.save_role_verdict(
        "ashby:runway-ml:42",
        RoleVerdict(role_title="Design Engineer", discipline="design",
                    reason="right discipline", hybrid_test="n/a", domain_fit="none",
                    hybrid_evidence=[], overlaps=["x"], gaps=[],
                    role_fit="strong"),
        model="m", prompt_sha="s")
    store.save_company_verdict(
        "runway", "Runway",
        CompanyVerdict(reason="r", domain_pull="present",
                       designs_how_things_work="present",
                       design_valued="present", stage_fit="unknown",
                       reachability="unknown", disqualifier=None,
                       company_fit="target"),
        model="m", prompt_sha="s")
    store.refresh_actions()
    assert store.action_counts() == {"apply_priority": 1}

    store.mark_closed(["ashby:runway-ml:42"], now="2026-09-30")

    assert store.action_counts() == {}


# --- purging rows Part 1's geography fix newly makes non-storable --------

def test_purge_removes_a_role_that_now_classifies_non_us(store):
    """Simulates a role stored under an older/looser geography pattern that
    now, correctly, reads `non_us` - exactly what a re-poll after the Part 1
    fix cannot itself fix, since `is_storable` just silently skips it going
    forward."""
    store.upsert_ats_role(role(), now="2026-09-29")
    store._conn.execute(
        "UPDATE postings SET location = 'London' WHERE id = ?",
        ("ashby:runway-ml:42",))
    store._conn.commit()

    removed = store.purge_unstorable_ats_roles()

    assert removed == ["ashby:runway-ml:42"]
    assert store.get("ashby:runway-ml:42") is None


def test_purge_leaves_a_still_storable_role_alone(store):
    store.upsert_ats_role(role(), now="2026-09-29")  # location "Remote" -> us

    removed = store.purge_unstorable_ats_roles()

    assert removed == []
    assert store.get("ashby:runway-ml:42") is not None


def test_purge_dry_run_reports_but_deletes_nothing(store):
    store.upsert_ats_role(role(), now="2026-09-29")
    store._conn.execute(
        "UPDATE postings SET location = 'London' WHERE id = ?",
        ("ashby:runway-ml:42",))
    store._conn.commit()

    removed = store.purge_unstorable_ats_roles(dry_run=True)

    assert removed == ["ashby:runway-ml:42"]
    assert store.get("ashby:runway-ml:42") is not None


def test_purge_never_touches_an_hn_row(store):
    """`source = 'hn'` is the project's only hand-labelled data."""
    from jobhunt.discovery.models import Posting
    store.upsert_posting(Posting(
        id="hn:1", source="hn", source_thread_id="9", company="Odin",
        company_normalised="odin", raw_text="x", apply_url=None, is_remote=True,
        geo_signal="us", posted_at="2026-09-01", first_seen="a", last_seen="a",
        status="new"))
    store._conn.execute("UPDATE postings SET location = 'London' WHERE id = 'hn:1'")
    store._conn.commit()

    removed = store.purge_unstorable_ats_roles()

    assert removed == []
    assert store.get("hn:1") is not None


def test_purge_never_deletes_a_role_with_a_match_verdict(store):
    """Someone paid for this verdict; it must be reported, not destroyed."""
    store.upsert_ats_role(role(), now="2026-09-29")
    store._conn.execute(
        "UPDATE postings SET location = 'London', matched_at = '2026-09-01'"
        " WHERE id = ?", ("ashby:runway-ml:42",))
    store._conn.commit()

    removed = store.purge_unstorable_ats_roles()

    assert removed == []
    assert store.get("ashby:runway-ml:42") is not None


def test_purge_never_deletes_a_role_with_a_role_score_verdict(store):
    store.upsert_ats_role(role(), now="2026-09-29")
    store._conn.execute(
        "UPDATE postings SET location = 'London', role_scored_at = '2026-09-01'"
        " WHERE id = ?", ("ashby:runway-ml:42",))
    store._conn.commit()

    removed = store.purge_unstorable_ats_roles()

    assert removed == []
    assert store.get("ashby:runway-ml:42") is not None


def test_closed_at_is_readable_on_the_posting_model(store):
    """A consumer that lists roles must be able to say WHY one is gone."""
    store.upsert_ats_role(role(), now="2026-09-29")
    assert store.get("ashby:runway-ml:42").closed_at is None

    store.mark_closed(["ashby:runway-ml:42"], now="2026-09-30")

    assert store.get("ashby:runway-ml:42").closed_at == "2026-09-30"
