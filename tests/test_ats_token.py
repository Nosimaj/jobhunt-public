"""Extracting an ATS board token from a job advertisement.

Measured 2026-09-28: 42 of 260 HN posts (18%) leak one. That registry is the
only thing this project has that names companies, so a missed token is a
company that never gets polled again.
"""

import pytest

from jobhunt.discovery.ats_token import PROVIDER_ORDER, extract


@pytest.mark.parametrize("text,expected", [
    ("apply at https://boards.greenhouse.io/wikimedia", ("greenhouse", "wikimedia")),
    ("https://job-boards.greenhouse.io/oklo/jobs/4001", ("greenhouse", "oklo")),
    ("see https://boards.greenhouse.io/embed/job_board?for=mattermost",
     ("greenhouse", "mattermost")),
    ("https://jobs.ashbyhq.com/runway-ml", ("ashby", "runway-ml")),
    ("https://jobs.ashbyhq.com/category-labs/f0e1", ("ashby", "category-labs")),
    ("https://jobs.lever.co/findigs", ("lever", "findigs")),
    ("https://jobs.lever.co/loftorbital/abc-123", ("lever", "loftorbital")),
])
def test_extracts_a_token(text, expected):
    assert extract(text) == expected


def test_returns_none_when_there_is_no_board():
    assert extract("email us at jobs@example.com") is None


def test_a_bare_provider_url_with_no_token_is_not_a_match():
    assert extract("we use https://jobs.lever.co/ for applications") is None


def test_two_different_boards_resolve_deterministically():
    """Review Focus 5. A post can link a Greenhouse board AND an Ashby one.
    Dict iteration order is stable in Python but is not a DECISION, so the
    precedence is stated explicitly and pinned here."""
    both = ("https://jobs.ashbyhq.com/acme and https://boards.greenhouse.io/acme")
    assert extract(both)[0] == PROVIDER_ORDER[0]
    assert extract(both) == extract(" ".join(reversed(both.split(" and "))))


def test_the_token_is_lowercased_for_a_stable_registry_key():
    assert extract("https://jobs.ashbyhq.com/Runway-ML") == ("ashby", "runway-ml")


def test_a_trailing_punctuation_mark_is_not_part_of_the_token():
    assert extract("apply: https://jobs.lever.co/findigs.") == ("lever", "findigs")


def test_extraction_needs_CLEANED_text_not_the_raw_hn_json():
    """A trap worth a test, because it fails SILENTLY as 'no tokens anywhere'.

    HN's /items/ endpoint is HTML-escaped: 254 of 260 posts contain `&#x2F;`
    instead of `/`, so a URL pattern run against the raw JSON matches ZERO of
    260 - verified 2026-09-29. `harvest` therefore reads `postings.raw_text`,
    which hn_parse has already unescaped, and never the fetched payload.
    """
    assert extract("https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;runway-ml") is None
    assert extract("https://jobs.ashbyhq.com/runway-ml") == ("ashby", "runway-ml")
