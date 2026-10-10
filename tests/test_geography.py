# tests/test_geography.py
"""Deterministic reachability. Geography is a FACT, not a judgement - it is
never rendered into a rubric, so every case here is a string fixture.

Fixtures are taken VERBATIM from the live corpus. This project has shipped six
substring false positives; `\\bME\\b` under re.I matching the pronoun "me" is
the one this module is most exposed to.
"""
import pytest

from jobhunt.matching.geography import reachability

METROS = ["Boston", "Cambridge, MA", "Somerville, MA", "Waltham",
          "Burlington, MA", "Newton, MA", "Andover, MA", "Nashua"]
STATES = ["MA", "NH", "VT", "ME"]


def reach(loc, wt=None):
    return reachability(loc, wt, METROS, STATES)


@pytest.mark.parametrize("loc", [
    "Remote", "REMOTE (Americas)", "Fully Remote", "United States (Remote)",
    "US/EU - Remote", "USA - Remote", "Remote (US)",
])
def test_plain_remote_is_remote(loc):
    assert reach(loc) == "remote"


@pytest.mark.parametrize("loc", [
    "NYC or Remote",
    "Dallas, TX | REMOTE (US) or HYBRID",
    "New York, NY | HYBRID (NYC) or REMOTE",
    "Remote (North America), SF, NYC",
])
def test_a_remote_option_wins_even_after_an_out_of_area_city(loc):
    """REVIEW FOCUS 2. All four are live values. A naive first-city parse
    would call these out_of_area and discard a reachable role."""
    assert reach(loc) == "remote"


def test_workplace_type_remote_wins_over_silent_location():
    assert reach("San Francisco, CA", "remote") == "remote"


@pytest.mark.parametrize("loc", [
    "Boston, MA", "Cambridge, MA", "Somerville, MA", "Andover, MA",
    "Waltham, Massachusetts", "Nashua, NH", "Burlington, VT", "Portland, ME",
])
def test_home_area_is_local(loc):
    assert reach(loc) == "local"


@pytest.mark.parametrize("loc", [
    "San Francisco, CA", "New York City, NY", "Austin, TX", "Seattle, WA",
    "London, UK",
])
def test_elsewhere_on_site_is_out_of_area(loc):
    assert reach(loc) == "out_of_area"


@pytest.mark.parametrize("loc", [None, "", "   ", "\n"])
def test_an_absent_location_is_unknown_never_out_of_area(loc):
    """REVIEW FOCUS 1. 1,024 of 1,796 live rows carry workplace_type
    'unknown'. Excluding the unknown would discard most of the corpus - the
    same shape as the prefilter's un-drop bug."""
    assert reach(loc) == "unknown"


def test_workplace_type_unknown_is_not_evidence_of_on_site():
    assert reach("", "unknown") == "unknown"


def test_a_sentence_is_not_local_and_the_pronoun_is_not_maine():
    """REVIEW FOCUS 3. `\\bME\\b` under re.I matches "me". State CODES are
    matched case-sensitively; state NAMES get re.I. A sentence with word-like
    content falls through to out_of_area (measured, see geography.py)."""
    assert reach("Tell me about the role") == "out_of_area"
    assert reach("me") == "unknown"


@pytest.mark.parametrize("loc", [
    "Hybrid", "Onsite", "On-site", "In office", "in-office", "Remote-first",
    "TBD", "N/A", "Multiple", "Field", "Various", "Hybrid / TBD",
])
def test_placeholder_and_workplace_words_are_unknown(loc):
    # "Remote-first" is caught earlier by the remote regex, so it is
    # `remote`; the rest must be `unknown`.
    expected = "remote" if loc.lower() == "remote-first" else "unknown"
    assert reach(loc) == expected


def test_a_placeholder_next_to_a_real_city_is_still_out_of_area():
    assert reach("Hybrid, Austin") == "out_of_area"


def test_maine_does_not_match_inside_domain():
    """REVIEW FOCUS 3, second half. `\\bmaine\\b` must not fire on 'domain'."""
    assert reach("Works across our domain team, Austin TX") == "out_of_area"


def test_a_full_state_name_is_matched_case_insensitively():
    assert reach("somewhere in massachusetts") == "local"


def test_an_out_of_area_city_in_a_home_state_is_still_local():
    """The state list is the backstop for towns not in the metro list."""
    assert reach("Pittsfield, MA") == "local"


def test_newton_does_not_match_newtonville():
    assert reach("Newtonville Road, Austin, TX") == "out_of_area"


@pytest.mark.parametrize("loc", [
    "United States", "USA", "United States only", "North America",
])
def test_a_bare_country_names_no_place_inside_the_home_area(loc):
    """The US CONTAINS the home area, so a country-level string is not
    `out_of_area`. It is how a US-wide posting is written."""
    assert reach(loc) == "unknown"


def test_virtual_is_a_remote_signal():
    assert reach("VIRTUAL US") == "remote"


@pytest.mark.parametrize("loc", [
    "Long Beach, California, United States",
    "New York, New York, United States",
    "Austin, North America, US",
])
def test_a_country_beside_a_real_place_is_still_out_of_area(loc):
    assert reach(loc) == "out_of_area"


# --- workplace_type must not override an explicit on-site location ---------
# Found 2026-10-06 on the live dashboard: 17 rendered rows carried
# workplace_type='remote' from the board's own structured field while their
# location text named a specific place. `reachability` short-circuited on
# workplace_type and never read the location, so a role whose location literally
# read "Work in person in our office @ 425 2nd St" sat in the apply bucket.

ONSITE_CONTRADICTIONS = [
    "Work in person in our office @ 425 2nd St, San Francisco",
    "San Francisco (On-site)",
    "In office, New York, NY",
    "Onsite - Austin, TX",
]


@pytest.mark.parametrize("loc", ONSITE_CONTRADICTIONS)
def test_an_explicit_onsite_location_overrides_workplace_type_remote(loc):
    """The board's structured field is a claim; the location text naming an
    office he must physically attend is evidence. Evidence wins."""
    assert reachability(loc, "remote", METROS, STATES) == "out_of_area"


def test_an_explicit_onsite_location_IN_the_home_area_is_still_reachable():
    """The override must not punish a local office - that is the one kind of
    on-site role he DOES want.

    Asserts REACHABLE rather than `local`: with `workplace_type='remote'` the
    board's own claim still wins the label, and whether a Somerville-office-
    plus-remote-flag role is "local" or "remote" is genuinely ambiguous from
    the data. Both are reachable, both score the location point, and neither
    is filtered - which is the invariant that matters."""
    for loc in ("In office, Somerville, MA", "Onsite - Boston, MA"):
        assert reachability(loc, "remote", METROS, STATES) != "out_of_area"
    # With no workplace_type to defer to, the home-area read is unambiguous.
    assert reachability("In office, Somerville, MA", None, METROS, STATES) == "local"


@pytest.mark.parametrize("loc", [
    "Europe, Middle East, Africa (EMEA)",
    "Asia-Pacific (APAC)",
    "Remote - EMEA",
    "LATAM (remote)",
])
def test_a_foreign_only_region_is_not_reachable(loc):
    """Remote, but not remote FOR HIM. Circle alone ships 7 EMEA rows that
    rendered as reachable."""
    assert reachability(loc, "remote", METROS, STATES) == "out_of_area"


@pytest.mark.parametrize("loc", [
    "North America, South America (AMER)",
    "North America",
    "United States (Remote)",
    "Remote (US)",
])
def test_an_americas_region_stays_reachable(loc):
    """AMER and North America CONTAIN the home area - they must not be caught
    by the foreign-region rule."""
    assert reachability(loc, "remote", METROS, STATES) == "remote"


def test_region_codes_are_matched_case_sensitively():
    """`\\bAPAC\\b` under re.I is safe, but the project rule is that
    abbreviations are uppercase in real usage. Guard the habit."""
    assert reachability("apac widgets team, Boston, MA", None,
                        METROS, STATES) == "local"
    assert reachability("Remote - APAC", None, METROS, STATES) == "out_of_area"


def test_a_plain_remote_role_with_no_location_is_unaffected():
    assert reachability("", "remote", METROS, STATES) == "remote"
    assert reachability("Remote", "remote", METROS, STATES) == "remote"


@pytest.mark.parametrize("loc", [
    "FULLY REMOTE (US / Canada / Europe / UK / Brazil / LATAM)",
    "REMOTE (AMER or EMEA time zones)",
    "NAMER, EMEA, APJ (Remote)",
    "Remote - US, UK, EU",
])
def test_a_foreign_region_does_not_exclude_when_the_americas_are_also_named(loc):
    """A role open to BOTH the Americas and EMEA is open to him. Excluding on
    the foreign token alone is a false negative, and false negatives are the
    expensive error here - this rule cost a stored `maybe_queue` row
    (Matcha.fm, "FULLY REMOTE (US / Canada / Europe / UK / Brazil / LATAM)")
    before it was fixed."""
    assert reachability(loc, "remote", METROS, STATES) == "remote"


def test_a_foreign_region_alone_still_excludes():
    """The rule must still bite when the Americas are NOT named."""
    for loc in ("Europe, Middle East, Africa (EMEA)", "Remote - EMEA", "APAC"):
        assert reachability(loc, "remote", METROS, STATES) == "out_of_area"
