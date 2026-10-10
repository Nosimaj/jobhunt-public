import json
from collections import Counter
from pathlib import Path

import pytest

from jobhunt.discovery.filters import classify, split
from jobhunt.discovery.hn_parse import parse_post
from jobhunt.discovery.models import HnRawPost, ParsedPost

FIXTURE = Path(__file__).parent / "fixtures" / "discovery" / "hn_thread_49522897.json"


@pytest.fixture(scope="module")
def parsed():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [parse_post(HnRawPost(item_id=str(c["id"]), text=c["text"]))
            for c in data["children"] if c.get("text")]


def _post(**kw):
    base = dict(id="hn:1", company="Acme", apply_url=None, is_remote=True,
                geo_signal="us", raw_text="Acme | Engineer | Remote US")
    return ParsedPost(**{**base, **kw})


def test_measured_four_way_split_accounts_for_every_post(parsed):
    result = split(parsed)
    counts = Counter(reason for _, reason in result.rejections)
    assert len(parsed) == 260
    assert counts["not_remote"] == 115
    assert counts["eu_only"] == 9
    assert counts["no_role_signal"] == 4
    assert len(result.candidates) == 132
    # The whole point: nothing falls through the cracks.
    assert len(result.candidates) + len(result.rejections) == 260


def test_measured_geo_split_accounts_for_every_post(parsed):
    """Re-measured after US_RE was split by case sensitivity. The old figures
    were taken with a regex whose re.I made \\bUS\\b match the pronoun "us",
    so they were never evidence of correctness."""
    counts = Counter(p.geo_signal for p in parsed)
    assert counts["us"] == 113
    assert counts["unknown"] == 138
    assert counts["eu_only"] == 9
    assert sum(counts.values()) == 260


def test_the_four_explicitly_eu_only_roles_are_rejected(parsed):
    """These four say "Remote (EU)" / "Remote (Europe)" in as many words and
    passed as candidates under the defective regex, because the prose
    contained the pronoun "us"."""
    by_id = {p.id: p for p in parsed}
    for pid in ("hn:49523950", "hn:49525468", "hn:49527115", "hn:49527344"):
        assert by_id[pid].geo_signal == "eu_only", pid
        assert classify(by_id[pid]) == "eu_only", pid


def test_not_remote_is_rejected():
    assert classify(_post(is_remote=False, raw_text="Acme | Engineer | Onsite NYC")) == "not_remote"


def test_eu_only_is_rejected():
    assert classify(_post(geo_signal="eu_only",
                          raw_text="Acme | Engineer | Remote (Europe)")) == "eu_only"


def test_no_role_signal_is_rejected():
    assert classify(_post(raw_text="Acme | Account Executive | Remote US")) == "no_role_signal"


def test_unknown_geo_is_a_candidate_not_a_rejection():
    # An unlabelled remote startup role is usually US-eligible, and passing it
    # to slice 3 costs almost nothing.
    assert classify(_post(geo_signal="unknown",
                          raw_text="Acme | Engineer | Remote")) is None


def test_candidate_returns_none():
    assert classify(_post()) is None


def test_rejection_order_not_remote_beats_eu_only():
    # A post that is neither remote nor US-eligible is reported as not_remote,
    # the more fundamental reason.
    p = _post(is_remote=False, geo_signal="eu_only", raw_text="Acme | Onsite London | CET")
    assert classify(p) == "not_remote"


def test_split_preserves_the_post_objects(parsed):
    result = split(parsed)
    ids = {p.id for p in result.candidates} | {p.id for p, _ in result.rejections}
    assert ids == {p.id for p in parsed}
