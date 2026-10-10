"""The two-axis fit models and the derived action.

The amendment's central claim is that role-fit and company-fit are INDEPENDENT
and that neither model picks the action - code does. These tests pin that
separation, because the failure it exists to prevent is the one the first eval
actually produced: Biobase scored `strong` on a role the human called "wrong
discipline, appealing ethos". Company appeal leaked into the role verdict.
"""

import pytest
from pydantic import ValidationError

from jobhunt.matching.fit import ACTION, derive_action
from jobhunt.matching.models import CompanyVerdict, RoleVerdict


def role(**kw):
    base = dict(role_fit="no", discipline="engineering", hybrid_test="fail", domain_fit="none",
                hybrid_evidence=[], overlaps=[], gaps=["no design ownership"],
                reason="pure backend role", role_title="Backend Engineer")
    return RoleVerdict(**{**base, **kw})


def company(**kw):
    base = dict(company_fit="neutral", domain_pull="unknown",
                designs_how_things_work="unknown", design_valued="unknown",
                stage_fit="unknown", reachability="unknown",
                disqualifier=None, reason="not enough signal in the posting")
    return CompanyVerdict(**{**base, **kw})


# --- the action matrix ----------------------------------------------------

@pytest.mark.parametrize("role_fit,company_fit,expected", [
    ("strong",  "target",  "apply_priority"),
    ("strong",  "neutral", "apply"),
    ("strong",  "no",      "apply_if_comp"),
    ("partial", "target",  "apply_and_outreach"),
    ("partial", "neutral", "maybe_queue"),
    ("partial", "no",      "drop"),
    ("no",      "target",  "outreach_lead"),
    ("no",      "neutral", "drop"),
    ("no",      "no",      "drop"),
])
def test_the_action_matrix(role_fit, company_fit, expected):
    assert derive_action(role_fit, company_fit) == expected


def test_the_matrix_covers_every_combination():
    # A missing pair would KeyError at runtime, mid-run, on one posting.
    assert len(ACTION) == 9


def test_a_wrong_role_at_a_great_company_is_an_outreach_lead_not_a_drop():
    """The whole reason for the second axis. Pomelo Care, Puma.tech and Sumble
    were human-`maybe` only because the company appealed; under one axis they
    had nowhere to go but `no`."""
    assert derive_action("no", "target") == "outreach_lead"


def test_a_great_role_at_a_disqualified_company_still_surfaces():
    # Not silently dropped - compensation can still make it worth a look, and
    # burying it would hide a judgement the human should make.
    assert derive_action("strong", "no") == "apply_if_comp"


# --- the role verdict -----------------------------------------------------

def test_an_engineering_role_that_fails_the_hybrid_test_cannot_be_strong():
    """The core steering rule, enforced in code so no prompt edit can bypass
    it. This is the guard that Biobase defeated."""
    with pytest.raises(ValidationError, match="hybrid"):
        role(role_fit="strong", discipline="engineering", hybrid_test="fail")


def test_an_engineering_role_that_only_weakly_passes_cannot_be_strong():
    with pytest.raises(ValidationError, match="hybrid"):
        role(role_fit="strong", discipline="engineering", hybrid_test="weak",
             hybrid_evidence=["some prototyping"])


def test_a_weak_hybrid_pass_may_be_partial():
    assert role(role_fit="partial", hybrid_test="weak",
                hybrid_evidence=["builds internal tools for designers"]
                ).role_fit == "partial"


def test_a_hybrid_pass_requires_quoted_evidence():
    """"Titles are NOT evidence." An empty evidence list with a `pass` is the
    model asserting a conclusion it did not source."""
    with pytest.raises(ValidationError, match="evidence"):
        role(role_fit="strong", hybrid_test="pass", hybrid_evidence=[])


def test_a_design_role_needs_no_hybrid_test():
    v = role(role_fit="strong", discipline="design", hybrid_test="n/a",
             overlaps=["design systems", "prototyping"])
    assert v.role_fit == "strong"


def test_a_design_engineering_role_needs_no_hybrid_test():
    v = role(role_fit="strong", discipline="design_engineering",
             hybrid_test="n/a", overlaps=["Python", "design systems"])
    assert v.role_fit == "strong"


def test_the_role_verdict_has_no_company_field():
    """Structural guarantee that company appeal cannot reach this axis: there
    is nowhere to put it. (`domain_fit` is deliberately allowed since
    2026-10-06: it judges the ROLE's duties, never the company.)"""
    assert not {f for f in RoleVerdict.model_fields
                if "company" in f or (f.startswith("domain")
                                      and f not in {"domain_fit",
                                                    "domain_evidence"})}


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        role(enthusiasm="high")


# --- the company verdict --------------------------------------------------

def test_a_disqualifier_forces_no():
    with pytest.raises(ValidationError, match="disqualifier"):
        company(company_fit="target", disqualifier="gambling")


def test_target_requires_domain_pull():
    """`target` is the verdict that promotes a rejected role into an outreach
    lead, so it is the expensive one to get wrong."""
    with pytest.raises(ValidationError, match="domain_pull"):
        company(company_fit="target", domain_pull="absent",
                designs_how_things_work="present", design_valued="present")


def test_target_requires_two_supporting_signals():
    with pytest.raises(ValidationError, match="two"):
        company(company_fit="target", domain_pull="present",
                designs_how_things_work="present")


def test_target_is_valid_with_domain_pull_and_two_signals():
    v = company(company_fit="target", domain_pull="present",
                designs_how_things_work="present", design_valued="present")
    assert v.company_fit == "target"


def test_both_core_signals_absent_forces_no():
    with pytest.raises(ValidationError, match="absent"):
        company(company_fit="neutral", designs_how_things_work="absent",
                design_valued="absent")


def test_all_unknown_is_neutral_not_no():
    """The realistic case while the only input is HN posting text: silence is
    not evidence of absence, and must not be scored as a rejection."""
    assert company().company_fit == "neutral"
