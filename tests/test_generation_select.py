"""Which roles render, which bullets are eligible, and under which lens.

The audience firewall lives here. profile.json holds 90 bullets; only those
that are lens-tagged AND user_approved may reach a document. The other 60 are
unreviewed LinkedIn exporter prose in the wrong voice.
"""

import pytest

from jobhunt.generation.select import (eligible_bullets, lens_for,
                                       relevant_case_studies, renderable_roles)


def bullet(bid, audience, approved=True, text="did a thing"):
    return {"id": bid, "text": text, "audience": audience,
            "user_approved": approved,
            "provenance": {"source": "resume", "snapshot_date": "2026-09-29"}}


def role(company="Acme", render="feature", bullets=None, dni=None):
    return {"id": company.lower(), "company": company,
            "title_of_record": "Designer", "title_presented": "Senior Designer",
            "start": "2020-01", "end": "2021-01", "render": render,
            "do_not_include": dni, "talking_points": None,
            "disclosure_rule": None, "bullets": bullets or []}


@pytest.mark.parametrize("discipline,expected", [
    ("design", "design"), ("engineering", "eng"),
    ("design_engineering", "eng"), ("product", "design"), ("other", "design"),
])
def test_lens_selection_is_deterministic(discipline, expected):
    assert lens_for(discipline) == expected


def test_an_untagged_bullet_is_never_eligible():
    """The firewall. Untagged bullets are unreviewed exporter prose."""
    r = role(bullets=[bullet("b1", "untagged")])
    assert eligible_bullets(r, "design") == []


def test_an_unapproved_bullet_is_never_eligible():
    r = role(bullets=[bullet("b1", "both", approved=False)])
    assert eligible_bullets(r, "design") == []


def test_a_both_bullet_is_eligible_under_either_lens():
    r = role(bullets=[bullet("b1", "both")])
    assert len(eligible_bullets(r, "design")) == 1
    assert len(eligible_bullets(r, "eng")) == 1


def test_a_design_bullet_is_not_eligible_under_the_eng_lens():
    r = role(bullets=[bullet("b1", "design")])
    assert eligible_bullets(r, "eng") == []
    assert len(eligible_bullets(r, "design")) == 1


def test_a_do_not_include_bullet_is_refused_even_when_otherwise_eligible():
    """REVIEW-CRITICAL. do_not_include names prose that must never appear in a
    generated document. Uses a FEATURE role with a matching eligible bullet -
    a minimal role would pass for the wrong reason."""
    r = role(render="feature", dni="Mentored other Design Team Members",
             bullets=[bullet("b1", "both", text="Mentored other Design Team Members daily."),
                      bullet("b2", "both", text="Shipped the design system.")])
    kept = [b["id"] for b in eligible_bullets(r, "design")]
    assert kept == ["b2"]


def test_do_not_include_matching_ignores_case_and_whitespace():
    r = role(render="feature", dni="mentored   other design team members",
             bullets=[bullet("b1", "both", text="Mentored other Design Team Members daily.")])
    assert eligible_bullets(r, "design") == []


def test_an_omit_role_never_renders():
    profile = {"roles": [role("Gone", render="omit"), role("Here")]}
    assert [r["company"] for r in renderable_roles(profile)] == ["Here"]


def test_a_minimal_role_renders_but_contributes_no_bullets():
    """REVIEW-CRITICAL. Until 2026-09-29 every minimal role happened to have
    zero eligible bullets, so the guard and the data agreed by accident. This
    uses a minimal role WITH eligible bullets."""
    r = role("Quiet", render="minimal", bullets=[bullet("b1", "both")])
    profile = {"roles": [r]}
    out = renderable_roles(profile)
    assert [x["company"] for x in out] == ["Quiet"]
    assert eligible_bullets(out[0], "design") == []


def test_case_studies_are_filtered_by_lens():
    from jobhunt.generation.case_studies import CaseStudy
    studies = [
        CaseStudy(title="D", url="u", pitch="p", lens="design", evidences="Acme"),
        CaseStudy(title="E", url="u", pitch="p", lens="eng", evidences="Acme"),
        CaseStudy(title="B", url="u", pitch="p", lens="both", evidences="Acme"),
    ]
    got = {c.title for c in relevant_case_studies(studies, "eng", {"Acme"})}
    assert got == {"E", "B"}


def test_a_case_study_for_an_unrendered_employer_is_dropped():
    """Citing work at a company the resume does not show invites a question
    the document cannot answer."""
    from jobhunt.generation.case_studies import CaseStudy
    studies = [CaseStudy(title="D", url="u", pitch="p", lens="design",
                         evidences="Hidden Corp")]
    assert relevant_case_studies(studies, "design", {"Acme"}) == []


def test_independent_work_is_always_relevant():
    """The Figma plugin has no employer, so it can never be orphaned."""
    from jobhunt.generation.case_studies import CaseStudy
    studies = [CaseStudy(title="Plugin", url="u", pitch="p", lens="eng")]
    assert len(relevant_case_studies(studies, "eng", set())) == 1
