"""The composite fit score and the page's shape. Pure - no server, no database.

The score is DERIVED from stored categories because the two-axis rubric has no
numeric fit value and adding one would mean re-scoring 996 roles. Spec section
10 records that the first version of this formula was wrong twice, caught by
running it over real rows; these tests pin both fixes.
"""

import pytest

from jobhunt.dashboard.views import (MAX_SCORE, build_page, fit_points,
                                     fit_score)

METROS = ["Boston", "Cambridge, MA"]


def role(**kw):
    base = dict(id="ashby:x:1", company="Acme", company_normalised="acme",
                role_title="Design Engineer", role_fit="strong",
                hybrid_test="pass", role_discipline="design_engineering",
                role_overlaps=["a", "b"], role_gaps=["c"],
                role_reason="Fits well.", location="Remote",
                apply_url="https://a/b", seniority_note=None,
                hybrid_evidence=["usability testing"], role_scored_at="2026-09-30",
                reach="remote", domain_fit="direct")
    return {**base, **kw}


#: (dimension, best) - MAX_SCORE must be exactly their sum.
_DIMENSION_MAXIMA = [("role", 4), ("hybrid", 2), ("company", 2),
                     ("evidence", 1), ("location", 1), ("domain", 2)]


def test_max_score_counts_every_dimension():
    assert MAX_SCORE == sum(best for _name, best in _DIMENSION_MAXIMA)
    assert MAX_SCORE == 12
    assert set(fit_points(role(), "target", METROS)) == {
        n for n, _ in _DIMENSION_MAXIMA}


def test_a_perfect_role_scores_the_maximum():
    assert fit_score(role(), "target", METROS) == MAX_SCORE


def test_a_direct_domain_scores_two_points():
    assert fit_points(role(domain_fit="direct"), "target", METROS)["domain"] == 2


def test_an_adjacent_domain_scores_one_point():
    assert fit_points(role(domain_fit="adjacent"), "target",
                      METROS)["domain"] == 1


def test_no_domain_scores_zero():
    assert fit_points(role(domain_fit="none"), "target", METROS)["domain"] == 0


def test_a_legacy_null_domain_is_zero_not_a_free_pass():
    assert fit_points(role(domain_fit=None), "target", METROS)["domain"] == 0
    p = fit_points({k: v for k, v in role().items() if k != "domain_fit"},
                   "target", METROS)
    assert p["domain"] == 0


def test_out_of_area_roles_never_reach_the_page():
    """Excluded before scoring, so they carry no verdict - but assert it at
    the view layer too, because a future query change could reintroduce them
    and the page is the thing he reads."""
    page = build_page([row(reach="out_of_area")], METROS)
    assert all(not b.roles and not b.companies for b in page.buckets)
    assert page.scored == 0


def test_a_not_yet_audited_role_stays_visible():
    """NULL reach means 'not audited', never 'unreachable'."""
    page = build_page([row(reach=None)], METROS)
    assert page.scored == 1


def test_reach_reaches_the_view():
    page = build_page([row(reach="local")], METROS)
    top = next(b for b in page.buckets if b.roles)
    assert top.roles[0].reach == "local"


def test_the_points_break_down_by_dimension():
    p = fit_points(role(), "target", METROS)
    assert p == {"role": 4, "hybrid": 2, "company": 2, "evidence": 1,
                 "location": 1, "domain": 2}


def test_a_partial_role_scores_less_on_the_role_dimension():
    assert fit_points(role(role_fit="partial"), "target", METROS)["role"] == 2


def test_hybrid_na_scores_for_a_design_role():
    """`n/a` means the hybrid gate does not apply, which for a design role is
    ideal rather than a deficiency."""
    p = fit_points(role(hybrid_test="n/a", role_discipline="design"),
                   "target", METROS)
    assert p["hybrid"] == 2


def test_hybrid_na_scores_nothing_for_a_non_design_role():
    """SPEC DEFECT 1, caught against real data: scoring `n/a` unconditionally
    put a 'Sr. Growth Manager, ABM & Enterprise' at 5/10."""
    p = fit_points(role(hybrid_test="n/a", role_discipline="other",
                        role_fit="partial"), "target", METROS)
    assert p["hybrid"] == 0


def test_a_role_fit_of_no_is_capped_at_three():
    """SPEC DEFECT 2, caught against real data: 'Join our Talent Community!' -
    not a job at all - scored 5/10 on company and location points."""
    assert fit_score(role(role_fit="no", hybrid_test="pass"),
                     "target", METROS) == 3


def test_the_cap_does_not_inflate_a_low_no():
    assert fit_score(role(role_fit="no", hybrid_test="fail",
                          role_overlaps=[], role_gaps=["x"],
                          location="Tulsa, OK", reach="out_of_area",
                          domain_fit="none"),
                     "no", METROS) == 0


def test_evidence_scores_only_when_overlaps_exceed_gaps():
    assert fit_points(role(role_overlaps=["a"], role_gaps=["b", "c"]),
                      "target", METROS)["evidence"] == 0


def test_a_commutable_location_scores():
    assert fit_points(role(location="Cambridge, MA", reach=None), "target",
                      METROS)["location"] == 1


def test_a_distant_location_scores_nothing():
    assert fit_points(role(location="Austin, TX", reach="out_of_area"),
                      "target", METROS)["location"] == 0


def test_a_missing_company_verdict_does_not_raise():
    """REVIEW FOCUS 1. Companies are scored separately; a role can be scored
    before its company is."""
    p = fit_points(role(), None, METROS)
    assert p["company"] == 0


# --- page assembly ---

def row(**kw):
    return {**role(), "company_fit": "target", **kw}


def test_buckets_are_ordered_by_what_to_do_next():
    page = build_page([row()], METROS)
    assert [b.action for b in page.buckets][0] == "apply_priority"


def test_outreach_leads_are_grouped_by_company():
    """18 leads across 8 companies is 8 tasks, not 18. Measured on the sample:
    Runway alone produced six."""
    rows = [row(id=f"ashby:runway:{i}", company="Runway",
                role_fit="no", hybrid_test="fail") for i in range(6)]
    rows.append(row(id="ashby:other:1", company="Other",
                    role_fit="no", hybrid_test="fail"))
    page = build_page(rows, METROS)
    outreach = next(b for b in page.buckets if b.action == "outreach_lead")
    assert len(outreach.companies) == 2
    runway = next(c for c in outreach.companies if c.company == "Runway")
    assert runway.role_count == 6


def test_non_outreach_buckets_list_roles_not_companies():
    page = build_page([row()], METROS)
    top = next(b for b in page.buckets if b.action == "apply_priority")
    assert len(top.roles) == 1 and top.companies == []


def test_roles_within_a_bucket_sort_by_score_descending():
    rows = [row(id="a", role_fit="partial", location="Austin, TX",
                role_overlaps=[], role_gaps=["x"]),
            row(id="b", role_fit="strong")]
    page = build_page(rows, METROS)
    bucket = next(b for b in page.buckets if b.roles)
    assert [r.id for r in bucket.roles] == ["b", "a"] or \
           all(bucket.roles[i].score >= bucket.roles[i+1].score
               for i in range(len(bucket.roles)-1))


def test_a_role_missing_an_axis_is_counted_as_not_yet_scored():
    """REVIEW FOCUS 2. Never silently vanish."""
    page = build_page([row(role_fit=None, role_scored_at=None)], METROS)
    assert page.not_yet_scored == 1
    assert all(not b.roles and not b.companies for b in page.buckets)


def test_the_reason_reaches_the_view():
    page = build_page([row(role_reason="A specific opportunity.")], METROS)
    top = next(b for b in page.buckets if b.roles)
    assert top.roles[0].reason == "A specific opportunity."


def test_the_page_carries_its_own_totals():
    page = build_page([row(), row(id="b", role_fit=None, role_scored_at=None)],
                      METROS)
    assert page.scored == 1 and page.not_yet_scored == 1


def test_an_unknown_reach_role_stays_visible():
    assert build_page([row(reach="unknown")], METROS).scored == 1


def test_location_points_follow_reach_when_it_is_set():
    """Billerica is `local` via home_states but not in home_metros."""
    r = role(location="Billerica, MA", reach="local")
    assert fit_points(r, "target", METROS)["location"] == 1
    assert fit_points(role(location="Austin, TX", reach="out_of_area"),
                      "target", METROS)["location"] == 0


def test_location_points_fall_back_to_commute_when_reach_is_null():
    assert fit_points(role(location="Boston", reach=None), "target",
                      METROS)["location"] == 1
    assert fit_points(role(location="Austin, TX", reach=None), "target",
                      METROS)["location"] == 0
