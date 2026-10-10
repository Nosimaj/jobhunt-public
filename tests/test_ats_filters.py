"""Which ATS roles get stored. Geography only - discipline is rubric A's job.

A discipline pre-filter was measured and rejected: it saved $4.39 and wrongly
dropped 'Senior Product Designer, Consumer Revenue' on the pattern 'revenue'.
Do not reintroduce one.
"""

import pytest

from jobhunt.discovery.ats_filters import geo_signal_for, is_storable
from jobhunt.discovery.models import AtsRole


def role(location="Remote", title="Design Engineer"):
    return AtsRole(id="ashby:t:1", provider="ashby", token="t", company="Co",
                   title=title, body="duties " * 60, location=location,
                   workplace_type="unknown", department="", apply_url=None,
                   posted_at="2026-09-01")


@pytest.mark.parametrize("loc", [
    "Remote", "San Francisco, CA", "New York City, NY", "United States",
    "Boston, MA", "Austin, TX (Remote)", "Remote - US",
    "Golden, CO", "Idaho Falls, ID", "Andover, MA", "Bethesda, MD",
])
def test_us_and_remote_locations_are_us(loc):
    assert geo_signal_for(loc) == "us"


@pytest.mark.parametrize("loc", [
    "London", "Stockholm, Sweden", "Berlin", "Bengaluru, India", "Zurich",
    "Toronto, Canada",
])
def test_foreign_locations_are_non_us(loc):
    assert geo_signal_for(loc) == "non_us"


def test_an_empty_location_is_unknown_not_non_us():
    """Review Focus 4. Absence of a location is not evidence of a foreign
    one. Treating it as non-US silently discards the role."""
    assert geo_signal_for("") == "unknown"
    assert geo_signal_for("   ") == "unknown"


def test_an_unknown_location_is_stored():
    assert is_storable(role(location="")) is True


def test_a_us_role_is_stored():
    assert is_storable(role(location="Boston, MA")) is True


def test_a_foreign_role_is_not_stored():
    assert is_storable(role(location="London")) is False


def test_a_role_with_no_body_is_not_stored():
    """Rubric A reads duties. A body-less role would be scored on its title
    and fail the Hybrid Test for lack of evidence, wasting the call."""
    thin = role().model_copy(update={"body": "apply here"})
    assert is_storable(thin) is False


def test_a_us_city_inside_a_longer_string_still_counts():
    assert geo_signal_for("Hybrid - Cambridge, MA or Remote") == "us"


def test_a_foreign_iso2_code_is_unknown_not_us():
    """A bare two-letter uppercase token is not evidence of a US state.

    Originally illustrated with "Budapest, HU" - but Budapest is now an
    unambiguous foreign-city token in its own right (see
    test_budapest_hu_is_non_us_now_that_the_city_is_known), so this uses a
    city that carries no recognisable signal at all. Falling through to
    unknown is the conservative, correct answer for a bare ISO-2 code; it is
    not evidence of being non-US either."""
    assert geo_signal_for("Plovdiv, BG") == "unknown"


@pytest.mark.parametrize("loc", [
    "Hybrid - London or Remote", "Remote - Berlin", "Stockholm or Remote",
])
def test_a_non_us_token_beats_a_us_or_remote_word_in_the_same_string(loc):
    """Pins the ordering inside geo_signal_for: the non-US check must run
    BEFORE the US/remote check, or a string naming both a foreign city and
    the word 'Remote' would wrongly resolve to 'us'."""
    assert geo_signal_for(loc) == "non_us"


# --- full US state names (measured gap A: ~187 roles misread as `unknown`) -

@pytest.mark.parametrize("loc", [
    "Long Beach, California", "Bethesda, Maryland", "Cape Canaveral, Florida",
    "Stennis Space Center, Mississippi", "Idaho Falls, Idaho",
    "Raleigh, North Carolina", "Lockhart, Texas", "Southeast Idaho",
])
def test_full_state_names_are_us(loc):
    assert geo_signal_for(loc) == "us"


def test_indianapolis_indiana_is_us():
    """'Indiana' contains 'india' as a substring. The foreign pattern's
    country group is \\b-bounded, so \\bindia\\b must not match inside
    'Indiana' - confirms that still holds once 'Indiana' is added to the
    US state names, and that the full state name itself resolves 'us'."""
    assert geo_signal_for("Indianapolis, Indiana") == "us"


def test_bare_vienna_is_unknown_not_us_or_non_us():
    """Vienna is ambiguous: Vienna, Austria and Vienna, Virginia (a real
    town near Washington DC) are both plausible. Decision: do NOT add a bare
    'vienna' token to either list - a country or state qualifier is
    required to disambiguate. `unknown` is the conservative answer for the
    bare token, consistent with the rest of this file's philosophy."""
    assert geo_signal_for("Vienna") == "unknown"


def test_vienna_austria_is_non_us():
    assert geo_signal_for("Vienna, Austria") == "non_us"


def test_vienna_virginia_is_us():
    assert geo_signal_for("Vienna, Virginia") == "us"


def test_bare_georgia_is_unknown_not_us():
    """Georgia is both a US state and a country. Decision: do NOT add
    'Georgia' to the US full state names - adding it would read
    'Tbilisi, Georgia' as a US location. `unknown` is the deliberate,
    documented answer for the ambiguous token; a real US city in Georgia
    with no other US signal (no state code, no known city name) stays
    `unknown` rather than risk a false `us` for the foreign country."""
    assert geo_signal_for("Tbilisi, Georgia") == "unknown"
    assert geo_signal_for("Some Town, Georgia") == "unknown"


# --- foreign tokens observed in the live data (measured gap B: ~33 roles) --

def test_toulouse_occitanie_is_non_us():
    assert geo_signal_for("Toulouse, Occitanie") == "non_us"


def test_abu_dhabi_is_non_us():
    assert geo_signal_for("Abu Dhabi") == "non_us"


def test_budapest_hu_is_non_us_now_that_the_city_is_known():
    assert geo_signal_for("Budapest, HU") == "non_us"


def test_freiburg_is_non_us():
    assert geo_signal_for("Freiburg") == "non_us"


def test_accented_sao_paulo_is_non_us():
    """The real board value carries the accent ('São Paulo'); the existing
    'sao paulo' pattern is plain ASCII and silently missed it."""
    assert geo_signal_for("São Paulo, BRA") == "non_us"


def test_asia_south_korea_is_non_us():
    assert geo_signal_for("Asia | South Korea") == "non_us"


@pytest.mark.parametrize("loc", [
    "North America", "Any Location", "Any", "Eastern Time zone",
])
def test_deliberately_left_unknown(loc):
    """North America includes Canada, so it is genuinely not a US signal.
    The others carry no geography at all."""
    assert geo_signal_for(loc) == "unknown"
