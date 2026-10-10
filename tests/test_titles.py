"""Boundary-safe title patterns, and the loose prefilter rule.

This project has shipped five substring false positives: \\bUS\\b matching the
pronoun "us", perks text matching the health domain, `ux` matching "Linux",
`revenue` matching "Consumer Revenue", and state codes failing to match state
names. Every pattern here is word-boundary anchored for that reason.
"""

import pytest

from jobhunt.matching.titles import (deny_reason, has_positive_signal,
                                     prefilter_drop)


@pytest.mark.parametrize("title", [
    "Senior Product Designer", "Design Engineer", "UX Researcher",
    "UI/UX Developer", "Staff Interaction Designer", "Prototyping Engineer",
    "Principal Product Designer", "Design Technologist",
    "Senior Software Engineer", "Backend Developer", "Solutions Architect",
])
def test_real_candidate_titles_carry_a_positive_signal(title):
    assert has_positive_signal(title) is True


@pytest.mark.parametrize("title", [
    "Senior Linux System Administrator", "Sr Linux Administrator",
    "Senior Embedded Software Engineer, Linux",
])
def test_ux_does_not_match_linux(title):
    """REVIEW FOCUS 2. Three live rows in the real data. 'Engineer' in the
    third IS a positive signal - the point is that `ux` must not be the
    reason."""
    from jobhunt.matching.titles import POSITIVE_RE
    found = {m.group(0).casefold() for m in POSITIVE_RE.finditer(title)}
    assert "ux" not in found


@pytest.mark.parametrize("title", [
    "Account Executive", "Enterprise Account Executive", "Recruiter",
    "Senior Accountant", "Commercial Counsel", "Paralegal",
    "Registered Nurse", "Machinist", "Warehouse Associate",
    "Payroll Specialist", "Executive Assistant",
])
def test_unambiguous_non_candidates_are_denied(title):
    assert deny_reason(title) is not None


def test_a_positive_signal_overrules_a_denylist_hit():
    """REVIEW FOCUS 1. THE case this design exists for. An earlier filter
    dropped this exact title on the pattern `revenue`."""
    title = "Senior Product Designer, Consumer Revenue"
    assert deny_reason(title) is not None      # the denylist does fire
    assert has_positive_signal(title) is True  # but it is overruled
    assert prefilter_drop(title) is None


@pytest.mark.parametrize("title", [
    "Senior Product Designer, Consumer Revenue",
    "Design Engineer, Sales Tooling",
    "Product Designer, Recruiting Platform",
    "Engineering Manager, Payroll",
])
def test_every_overrule_case_survives(title):
    assert prefilter_drop(title) is None


@pytest.mark.parametrize("title", [
    "Account Executive", "Mid-Market Account Executive",
    "Senior Revenue Accountant", "Technical Recruiter",
])
def test_obvious_non_fits_are_dropped_with_a_reason(title):
    reason = prefilter_drop(title)
    assert reason and len(reason) > 3


def test_an_unrecognised_title_is_kept():
    """Loose: the default is to spend money, not to discard."""
    assert prefilter_drop("Chief Vibes Officer") is None


def test_an_empty_title_is_kept():
    """REVIEW FOCUS 5, and the inverted-condition trap."""
    assert prefilter_drop("") is None
    assert prefilter_drop(None) is None


def test_the_reason_names_what_matched():
    reason = prefilter_drop("Enterprise Account Executive")
    assert "account executive" in reason.casefold()


def test_matching_is_case_insensitive():
    assert prefilter_drop("ACCOUNT EXECUTIVE") is not None
    assert has_positive_signal("senior product designer") is True


def test_a_denylist_word_inside_a_longer_word_does_not_fire():
    """Boundaries both ways: 'sales' must not fire on 'Salesforce Engineer',
    which is an engineering role."""
    assert prefilter_drop("Salesforce Platform Engineer") is None


# --- 2026-10-01 additions, measured against 1,792 open ATS roles -----------
# 659 distinct titles were classified by neither list. These patterns cover
# the largest unambiguous families in that residue. Every one was read off
# the real data first; none was guessed.

@pytest.mark.parametrize("title", [
    "Member of Technical Staff",
    "Member of Technical Staff (Inference)",
    "Member of Technical Staff, Frontend",
    "Member of Technical Staff (Hardware)",
])
def test_member_of_technical_staff_is_a_positive_signal(title):
    """27 live rows. These are engineering roles at AI labs; they carry no
    'engineer' token, so without this they are invisible to classification
    and unprotected by the override."""
    assert has_positive_signal(title) is True


@pytest.mark.parametrize("title", [
    "Factory Test Technician II - 1st Shift",
    "Friction Stir Welding Technician I, First Shift",
    "Compounding Pharmacy Technician - Boynton Beach, FL",
    "HVAC Technician",
    "Facilities Technician",
])
def test_technicians_are_denied(title):
    assert prefilter_drop(title) is not None


@pytest.mark.parametrize("title", [
    "CNC Programmer II, Second Shift",
    "Inventory Specialist II, First Shift",
    "Line Lead, Night Shift",
    "Shift Supervisor",
])
def test_shift_designations_are_denied(title):
    """Only the shift DESIGNATION forms, never a bare 'shift' - a product
    called Shift is a plausible title fragment."""
    assert prefilter_drop(title) is not None


def test_a_bare_shift_token_does_not_fire():
    assert deny_reason("Head of Shift Left Security") is None


@pytest.mark.parametrize("title", [
    "Founding GTM", "Head of GTM Enablement", "GTM Strategy & Operations",
    "Sr. GTM Marketing", "Staff GTM Analyst",
])
def test_gtm_is_denied(title):
    assert prefilter_drop(title) is not None


def test_gtm_is_matched_case_sensitively():
    """Gotcha 1 of slice 2: `re.I` on an abbreviation is a bug. `\\bUS\\b`
    with `re.I` matched the pronoun 'us' in 39 of 260 posts. Abbreviations
    are uppercase in real titles, so this one does not get `re.I`."""
    assert deny_reason("Gtm Lead") is None
    assert deny_reason("Head of GTM Enablement") is not None


@pytest.mark.parametrize("title", [
    "Talent Partner", "Senior Talent Partner", "Executive Talent Partner",
    "Talent Coordinator (Temp)", "Talent Associate", "Talent Strategy Lead",
    "Join Our Talent Community!", "General Interest - Talent Network",
    "Talent Pool (US)",
])
def test_the_talent_family_is_denied(title):
    """Includes the three non-roles - a talent pool/community/network posting
    is an open-ended expression of interest, not a job."""
    assert prefilter_drop(title) is not None


@pytest.mark.parametrize("title", [
    "Head of People", "People Operations", "People Operations Manager",
    "Sr. People Business Partner", "Staff People Business Partner",
])
def test_the_people_function_is_denied(title):
    assert prefilter_drop(title) is not None


@pytest.mark.parametrize("title", [
    "Business Operations Lead", "Business Operations Manager",
    "Accounting Operations Manager", "Clinical Strategy and Operations Lead",
])
def test_operations_lead_and_manager_are_denied(title):
    assert prefilter_drop(title) is not None


@pytest.mark.parametrize("title", [
    "Program Manager", "Marketing Program Manager", "Security Program Manager",
    "Senior Staff Technical Program Manager", "Sr Program Manager, Metrology",
])
def test_program_managers_are_denied(title):
    assert prefilter_drop(title) is not None


@pytest.mark.parametrize("title", [
    "Design Technician",
    "Prototyping Technician",
    "Design Operations Lead",
    "Research Operations Manager",
    "Design Program Manager",
    "Product Design Program Manager",
    "Design Engineer, GTM Tools",
    "Product Designer, People Operations",
    "UX Researcher, Talent Acquisition",
])
def test_the_override_still_saves_every_new_family(title):
    """The whole point of the design. Each of these matches a NEW denylist
    term and must survive on its positive signal."""
    assert deny_reason(title) is not None
    assert prefilter_drop(title) is None
