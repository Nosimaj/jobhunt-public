# tests/test_reconcile_gaps.py
from pathlib import Path

import pytest

from jobhunt.models import SourceRef
from jobhunt.overrides import Overrides, RoleOverride, load_overrides
from jobhunt.parse_linkedin_pdf import normalize_lines, parse_text
from jobhunt.models import ResumeProfile
from jobhunt.reconcile import chronology_gaps, reconcile

FIXTURES = Path(__file__).parent / "fixtures"
SOURCES = [SourceRef(role="pdf", path="/x/Profile.pdf", sha256="a" * 64,
                     snapshot_date="2026-09-19"),
           SourceRef(role="resume", path="/x/resume.html", sha256="b" * 64,
                     snapshot_date="2026-09-19")]


def _build(overrides):
    raw = (FIXTURES / "pdf_text_real.txt").read_text(encoding="utf-8")
    pdf_roles, conflicts = parse_text(raw)
    html = ResumeProfile.model_validate_json((FIXTURES / "resume_profile.json").read_text())
    # pdf_header_lines is required: reconcile() must never be able to emit a
    # blank identity because a caller forgot the kwarg.
    return reconcile(pdf_roles, html, overrides, SOURCES,
                     pdf_header_lines=normalize_lines(raw),
                     today="2026-09-19", pdf_conflicts=conflicts)


def test_manual_role_lands_in_chronological_position():
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    profile, _ = _build(ov)
    ids = [r.id for r in profile.roles]
    manual = next(r for r in profile.roles if r.company == "Northwind Traders")
    bbh = next(r for r in profile.roles if r.company == "Cartwell Health")
    swr = next(r for r in profile.roles if r.company == "Spokewell Repair")
    assert ids.index(swr.id) < ids.index(manual.id) < ids.index(bbh.id)


def test_manual_bullets_carry_manual_provenance():
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    profile, _ = _build(ov)
    manual = next(r for r in profile.roles if r.company == "Northwind Traders")
    assert all(b.provenance.source == "manual" for b in manual.bullets)
    assert manual.do_not_include == "Synthetic exclusion note."


def test_manual_role_closes_the_recent_gap():
    """Northwind Traders (2023-05..2024-04) bridges Cartwell Health and SWR.

    Three older gaps remain and are expected: 74 months (2005-08 -> 2011-10),
    5 months (2012-06 -> 2012-11), 3 months (2019-11 -> 2020-02).
    """
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    profile, report = _build(ov)
    assert sorted(g.months for g in profile.chronology_gaps) == [3, 5, 74]
    assert report.chronology_gaps == profile.chronology_gaps


def test_removing_the_mid_career_role_produces_a_fourth_gap():
    profile, report = _build(Overrides())          # no manual_roles
    assert sorted(g.months for g in profile.chronology_gaps) == [3, 5, 14, 74]
    recent = next(g for g in profile.chronology_gaps if g.months == 14)
    after = next(r for r in profile.roles if r.id == recent.after_role_id)
    before = next(r for r in profile.roles if r.id == recent.before_role_id)
    assert after.company == "Cartwell Health" and after.end == "2023-03"
    assert before.company == "Spokewell Repair" and before.start == "2024-05"
    assert report.chronology_gaps == profile.chronology_gaps


def test_concurrent_roles_are_merged_before_gaps_are_measured():
    """The 2012 gap must measure from the LATEST overlapping end, not the
    nearest consecutive one. Initech Bank's two engagements end 2012-03 and
    2012-06; measuring from 2012-03 would wrongly report 8 months."""
    profile, _ = _build(Overrides())
    gap = next(g for g in profile.chronology_gaps if g.months == 5)
    after = next(r for r in profile.roles if r.id == gap.after_role_id)
    assert after.end == "2012-06"


def test_a_chronology_gap_does_not_block_completeness():
    profile, report = _build(Overrides())
    assert profile.chronology_gaps
    assert report.blocking_gaps == []
    assert profile.complete is True
    assert all(g.blocking is False for g in profile.gaps)


def test_render_omit_retains_its_disclosure_rule(tmp_path):
    f = tmp_path / "omit.yaml"
    f.write_text(
        "manual_roles:\n"
        "  - company: Northwind Traders\n"
        "    title_of_record: Staff Designer\n"
        "    title_presented: Staff Designer\n"
        "    start: 2023-05\n"
        "    end: 2024-04\n"
        "    render: omit\n"
        "    disclosure_rule: Must be disclosed on complete-history forms.\n",
        encoding="utf-8")
    profile, report = _build(load_overrides(f))
    role = next(r for r in profile.roles if r.company == "Northwind Traders")
    assert role.render == "omit"
    assert role.disclosure_rule == "Must be disclosed on complete-history forms."
    assert any("omit" in d and "Northwind" in d for d in report.render_directives)


def test_unapproved_bullets_are_listed_for_signoff():
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    _, report = _build(ov)
    assert len(report.unapproved_bullets) == 1
    assert "Synthetic bullet two" in report.unapproved_bullets[0]


def test_fixture_independent_engagement_lands_with_supplement_only():
    """Spec §9 requires this against `profile.independent_engagements`.

    The report line alone is not enough: `supplement_only` is a property of
    the merged engagement, and it is what stops slice 4 presenting an
    engagement as the employment of record for a period in which a concurrent
    full-time role already exists.
    """
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    profile, _ = _build(ov)
    assert len(profile.independent_engagements) == 1
    acme = profile.independent_engagements[0]
    assert acme.client == "Acme Co"
    assert acme.work == "Brand and print design"
    assert (acme.start, acme.end) == ("2022-03", "2022-05")
    assert acme.paid is True
    assert acme.dates_confirmed is False
    assert acme.supplement_only is True
    assert acme.id == "acme-co-2022-03"
    # An engagement is never promoted into `roles`.
    assert not any(r.company == "Acme Co" for r in profile.roles)


def test_unconfirmed_engagement_dates_are_listed():
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    _, report = _build(ov)
    assert any("Acme Co" in u for u in report.unconfirmed_dates)


def test_tagging_worklist_lists_untagged_roles_recent_first():
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    _, report = _build(ov)
    assert len(report.tagging_worklist) == 12
    assert "Contoso" in report.tagging_worklist[0]


def test_gap_helper_merges_spans_and_uses_a_two_month_threshold():
    from jobhunt.models import Role

    def role(rid, start, end):
        return Role(id=rid, company=rid, title_of_record="t",
                    title_presented="t", start=start, end=end)

    # Two months apart: contiguous enough, not reported.
    assert chronology_gaps([role("b", "2024-03", "present"),
                            role("a", "2021-01", "2024-01")], "2026-09-19") == []
    # Six months apart: reported.
    gaps = chronology_gaps([role("b", "2024-07", "present"),
                            role("a", "2021-01", "2024-01")], "2026-09-19")
    assert len(gaps) == 1 and gaps[0].months == 6
    # A third role overlapping the window closes it.
    covered = [role("b", "2024-07", "present"), role("a", "2021-01", "2024-01"),
               role("c", "2023-06", "2024-08")]
    assert chronology_gaps(covered, "2026-09-19") == []
    # Order of the input must not matter.
    assert chronology_gaps(list(reversed(covered)), "2026-09-19") == []


# ---- role_overrides: render on a PARSED (PDF-sourced) role -----------------

def test_role_override_sets_render_on_a_parsed_role():
    ov = Overrides(role_overrides=[
        RoleOverride(company="Tidewater Corporation", render="minimal")])
    profile, report = _build(ov)
    tidewater = next(r for r in profile.roles if r.company == "Tidewater Corporation")
    assert tidewater.render == "minimal"
    assert any("Tidewater Corporation" in d and "render=minimal" in d
               for d in report.render_directives)


def test_role_override_naming_an_unknown_employer_is_reported():
    ov = Overrides(role_overrides=[
        RoleOverride(company="Totally Fake Employer Inc", render="omit")])
    profile, report = _build(ov)
    assert any("Totally Fake Employer Inc" in c for c in report.conflicts)
    # Nothing was actually omitted: every parsed role keeps its default render.
    assert all(r.render == "feature" for r in profile.roles
               if r.company != "Totally Fake Employer Inc")


def test_role_override_with_a_mismatched_start_is_reported_not_silently_applied():
    ov = Overrides(role_overrides=[
        RoleOverride(company="Tidewater Corporation", start="1999-01", render="omit")])
    profile, report = _build(ov)
    tidewater = next(r for r in profile.roles if r.company == "Tidewater Corporation")
    # The start didn't match, so the override must NOT have applied.
    assert tidewater.render == "feature"
    assert any("Tidewater Corporation" in c and "1999-01" in c for c in report.conflicts)


def _overrides_with_jbl_omitted():
    """Base synthetic overrides (which already close the 14-month gap via a
    manual role) plus the role_override under test, mirroring how the real
    overrides.yaml combines a manual role with a role_override."""
    base = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    return base.model_copy(update={"role_overrides": [
        RoleOverride(company="Sam Example LLC", render="omit",
                     reason="1994-2005, predates the current career.")]})


def test_omitting_sam_example_llc_leaves_exactly_two_gaps():
    profile, report = _build(_overrides_with_jbl_omitted())
    assert sorted(g.months for g in profile.chronology_gaps) == [3, 5]
    assert report.chronology_gaps == profile.chronology_gaps


def test_omitted_role_remains_in_profile_roles_with_render_omit():
    """Omission is a rendering decision, never a data deletion."""
    profile, report = _build(_overrides_with_jbl_omitted())
    jbl = next((r for r in profile.roles if r.company == "Sam Example LLC"), None)
    assert jbl is not None, "the omitted role must still be present in profile.roles"
    assert jbl.render == "omit"
    assert any("Sam Example LLC" in d and "render=omit" in d
               and "1994-2005" in d for d in report.render_directives)
