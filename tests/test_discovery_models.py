import pytest
from pydantic import ValidationError

from jobhunt.discovery.models import (DiscoveryRun, HnRawPost, HnThreadRef,
                                      ParsedPost, Posting)


def _parsed(**kw):
    base = dict(id="hn:1", company="Acme", apply_url=None, is_remote=True,
                geo_signal="us", raw_text="Acme | Engineer | Remote US")
    return ParsedPost(**{**base, **kw})


def test_parsed_post_rejects_unknown_geo_signal():
    with pytest.raises(ValidationError):
        _parsed(geo_signal="mars")


def test_parsed_post_accepts_the_three_geo_signals():
    for g in ("us", "eu_only", "unknown"):
        assert _parsed(geo_signal=g).geo_signal == g


def test_posting_rejects_unknown_reject_reason():
    with pytest.raises(ValidationError):
        Posting(id="hn:1", source="hn", source_thread_id="9", company="Acme",
                company_normalised="acme", raw_text="x", apply_url=None,
                is_remote=True, geo_signal="us", posted_at="2026-09-01",
                first_seen="2026-09-20", last_seen="2026-09-20",
                status="rejected", reject_reason="because_i_said_so")


def test_posting_status_is_a_plain_string_so_later_slices_can_extend_it():
    p = Posting(id="hn:1", source="hn", source_thread_id="9", company="Acme",
                company_normalised="acme", raw_text="x", apply_url=None,
                is_remote=True, geo_signal="us", posted_at="2026-09-01",
                first_seen="2026-09-20", last_seen="2026-09-20",
                status="scored_by_slice_3")
    assert p.status == "scored_by_slice_3"


def test_models_forbid_extra_fields():
    with pytest.raises(ValidationError):
        HnThreadRef(id="1", title="t", created_at="2026-09-01", surprise=1)


def test_raw_post_and_run_defaults():
    assert HnRawPost(item_id="7", text="hi").created_at is None
    run = DiscoveryRun(started_at="2026-09-20T00:00:00")
    assert (run.posts_seen, run.candidates, run.rejected, run.new_postings) == (0, 0, 0, 0)
    assert run.error is None and run.finished_at is None
