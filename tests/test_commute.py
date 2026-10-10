"""Commute proximity: a RANKING signal, never a gate and never a rubric input.

Rubric A is deliberately not told where he lives. If it were, it would score a
San Francisco Product Designer as `no` on "a relocation he is excluded from" -
suppressing the role exactly as a company gate would, only more quietly. He
decides what he will travel for; the rubric decides whether the job is right.
"""

import pytest

from jobhunt.matching.commute import is_commutable
from jobhunt.matching.search_config import load_search_config

METROS = load_search_config().home_metros


@pytest.mark.parametrize("loc", [
    "Boston, MA", "Cambridge, MA", "Somerville, MA", "Boston", "Waltham, MA",
])
def test_local_locations_are_commutable(loc):
    assert is_commutable(loc, METROS) is True


@pytest.mark.parametrize("loc", [
    "San Francisco, CA", "New York City, NY", "Austin, TX", "London",
])
def test_distant_locations_are_not(loc):
    assert is_commutable(loc, METROS) is False


def test_remote_is_not_commutable_because_it_is_not_a_commute():
    """Remote is better than commutable, not a kind of it. Conflating them
    would let a remote role be ranked as though it required a drive."""
    assert is_commutable("Remote", METROS) is False


def test_an_empty_location_is_not_commutable():
    assert is_commutable("", METROS) is False


def test_matching_is_case_insensitive():
    assert is_commutable("boston, ma", METROS) is True


def test_a_substring_of_a_longer_city_does_not_match():
    """'Newton, MA' is in the list; 'Newtonville' and 'New York' must not
    match it."""
    assert is_commutable("New York, NY", METROS) is False


# --- full state names, not just two-letter codes (coordinator finding 3) --
#
# Measured against all 1,102 live ATS board locations on 2026-09-29: BOTH
# forms are common - "Long Beach, California, United States" (158 roles),
# "Bethesda, Maryland" (41) alongside "Andover, MA" (30), "Bethesda, MD" (20).
# `\bcambridge, ma\b` cannot match "Cambridge, Massachusetts" because the "ma"
# in "Massachusetts" is followed by a word character, so a Boston-area role
# written out in full was silently ranked as distant. The state must not be
# stripped outright, though - "Cambridge, UK" must still not match.

@pytest.mark.parametrize("loc,expected", [
    ("Cambridge, Massachusetts", True),
    ("Cambridge, MA", True),
    ("Cambridge, UK", False),
    ("Boston, Massachusetts", True),
])
def test_full_state_names_are_tolerated_alongside_abbreviations(loc, expected):
    assert is_commutable(loc, METROS) is expected
