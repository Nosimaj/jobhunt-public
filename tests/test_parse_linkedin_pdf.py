from collections import Counter
from pathlib import Path

import pytest

from jobhunt.parse_linkedin_pdf import (duration_months, normalize_lines,
                                        parse_text, span_months)

FIXTURES = Path(__file__).parent / "fixtures"
REAL = (FIXTURES / "pdf_text_real.txt").read_text(encoding="utf-8")
BAD_DURATION = (FIXTURES / "pdf_text_bad_duration.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed():
    return parse_text(REAL)


def test_nbsp_is_normalized_and_footers_stripped():
    lines = normalize_lines("a\xa0b\n   \nPage 3 of 6\nc")
    assert lines == ["a b", "c"]


def test_finds_twenty_roles(parsed):
    roles, _ = parsed
    assert len(roles) == 20


def test_company_distribution(parsed):
    roles, _ = parsed
    assert Counter(r.company for r in roles) == {
        "Contoso": 6, "Globex Telecom": 2, "Initech Bank": 2,
        "Spokewell Repair": 1, "Quillon Labs": 1, "Cartwell Health": 1,
        "Lumen Devices": 1, "Brightwater, Inc": 1, "Tidewater Corporation": 1,
        "Fabrikam Mobile": 1, "The Alder Group": 1,
        "Contoso Arcadia": 1, "Sam Example LLC": 1,
    }


def test_group_header_roles_inherit_the_group_company(parsed):
    roles, _ = parsed
    by_title = {r.title: r for r in roles}
    # These sit under bare-duration group headers.
    assert by_title["UI/UX Design Developer"].company == "Contoso"
    assert by_title["Technical PM for UI/UX"].company == "Initech Bank"

    # "Technical PM + UI/UX - Team Lead" is not unique: the real group-header
    # role (Nov 2011-Apr 2012, under the Contoso/6 months header) and its
    # continuation (Nov 2011-Feb 2012) share the same title after whitespace
    # collapsing, so a title-keyed dict silently drops one of them and this
    # test would stop exercising group-header resolution specifically. Make
    # the collision visible and disambiguate by start/end instead.
    team_lead_roles = [r for r in roles if r.title == "Technical PM + UI/UX - Team Lead"]
    assert len(team_lead_roles) == 2, (
        "expected two roles sharing this title (group-header role + "
        "continuation); fixture layout may have changed")
    by_span = {(r.start, r.end): r for r in team_lead_roles}
    assert by_span[("2011-11", "2012-04")].company == "Contoso"  # group-header role
    assert by_span[("2011-11", "2012-02")].company == "Contoso"  # continuation role


def test_group_header_duration_is_not_mistaken_for_a_role(parsed):
    roles, _ = parsed
    assert not any(r.company.endswith("months") for r in roles)
    assert not any(r.title.endswith("months") for r in roles)


def test_first_and_last_role_fields(parsed):
    roles, _ = parsed
    first, last = roles[0], roles[-1]
    assert (first.company, first.title) == ("Spokewell Repair", "Co-Owner")
    assert (first.start, first.end) == ("2024-05", "present")
    assert first.location == "Newton, MA"
    assert (last.company, last.title) == ("Sam Example LLC", "Principle")
    assert (last.start, last.end) == ("1994-09", "2005-08")


def test_bullets_reassembled_across_wrapped_lines(parsed):
    roles, _ = parsed
    swr = roles[0]
    assert len(swr.bullets) == 4
    assert swr.bullets[0].startswith("Zisebilenofasi and lorelasa a mobile bike repair")
    assert swr.bullets[0].endswith("24-zako baropudage.")


def test_markerless_bullets_are_split_on_sentences(parsed):
    roles, _ = parsed
    tidewater = next(r for r in roles if r.company == "Tidewater Corporation")
    assert len(tidewater.bullets) == 5
    assert tidewater.bullets[0] == ("Domegabut new products and budazuvimep in the "
                               "zinerako gazazifidiv and health lizukodaso.")


def test_leading_unmarked_lines_are_kept_not_dropped(parsed):
    """`Project:` / `Desc:` lines above the first bullet marker must survive.

    Four engagements in the export open with unmarked lines before their first
    "•" marker. Those lines are the only description those roles have. The
    marked-block branch used to discard them with no conflict.
    """
    roles, _ = parsed
    wf = next(r for r in roles
              if r.company == "Initech Bank" and r.start == "2011-10")
    assert wf.bullets[0] == "Project: CIO Zodasafas Baledufov"
    assert wf.bullets[1] == (
        "Desc: Zodasafas Baledufov tazu by CIO to biguput Nutudufa Sevuliropabo")
    assert wf.bullets[2].startswith("Ralizib design team and UX designer")
    # The four affected roles, each with two leading unmarked lines.
    affected = [r for r in roles
                if any(b.startswith("Project:") for b in r.bullets)]
    assert len(affected) == 4
    assert all(b.startswith("Project:") for r in affected for b in r.bullets[:1])
    assert all(r.bullets[1].startswith("Desc:") for r in affected)


def test_total_bullet_count_is_exactly_eight_higher_than_marked_only(monkeypatch):
    """Regression guard on the measured +8, computed rather than hard-coded.

    `_marked_only` is the pre-fix implementation: a line before the first
    marker falls through the continuation branch while the bullet list is
    still empty, and is lost. Swapping it back in and re-parsing the same
    fixture measures exactly how much content used to be dropped.
    """
    import jobhunt.parse_linkedin_pdf as mod

    original = mod._split_bullets

    def _marked_only(block):
        if not block:
            return []
        if any(line.startswith(mod._BULLET_MARKERS) for line in block):
            bullets = []
            for line in block:
                if line.startswith(mod._BULLET_MARKERS):
                    bullets.append(
                        [line.lstrip("".join(mod._BULLET_MARKERS)).strip()])
                elif bullets:                    # the bug: silently drops
                    bullets[-1].append(line)     # leading unmarked lines
            return [" ".join(parts).strip() for parts in bullets]
        return original(block)                   # unmarked branch is unchanged

    roles_now, _ = mod.parse_text(REAL)
    monkeypatch.setattr(mod, "_split_bullets", _marked_only)
    roles_old, _ = mod.parse_text(REAL)

    assert len(roles_now) == 20 and len(roles_old) == 20
    total_now = sum(len(r.bullets) for r in roles_now)
    total_old = sum(len(r.bullets) for r in roles_old)

    assert total_old == 52, "marked-only baseline changed; fixture may have moved"
    assert total_now == 60
    assert total_now - total_old == 8


def test_no_bullet_contains_a_page_footer(parsed):
    roles, _ = parsed
    assert not any("Page " in b and " of " in b for r in roles for b in r.bullets)


def test_clean_fixture_reports_no_duration_conflicts(parsed):
    _, conflicts = parsed
    assert conflicts == []


def test_duration_mismatch_is_reported_not_corrected():
    roles, conflicts = parse_text(BAD_DURATION)
    assert len(roles) == 20
    tidewater = next(r for r in roles if r.company == "Tidewater Corporation")
    assert tidewater.start == "2016-12" and tidewater.end == "2019-11"   # dates untouched
    assert any("Tidewater" in c and "2 years" in c for c in conflicts)


def test_present_roles_are_not_duration_checked():
    _, conflicts = parse_text(REAL)
    assert not any("Spokewell Repair" in c for c in conflicts)


def test_unresolvable_employer_raises_value_error():
    # No company line, no group-header duration line, nothing above the
    # title at all -- the first role in the text has no way to resolve an
    # employer. This must be a loud failure (raise), never a silently
    # skipped/dropped role.
    text = "Mystery Role\nMay 2024 - Present (2 years 5 months)\n"
    with pytest.raises(ValueError, match="Mystery Role"):
        parse_text(text)


def test_duration_and_span_arithmetic():
    assert duration_months("1 year 10 months") == 22
    assert duration_months("3 years") == 36
    assert duration_months("11 years") == 132
    assert duration_months("6 months") == 6
    assert span_months("2021-06", "2023-03") == 22
    assert span_months("2016-12", "2019-11") == 36
