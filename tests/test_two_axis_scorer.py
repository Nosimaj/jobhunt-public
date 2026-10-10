"""The two scorers.

Both reuse the existing `Scorer`'s error handling, which encodes several
hard-won failure modes (truncation arriving as a ValidationError rather than a
max_tokens stop, refusals, the retryable/non-retryable split). Duplicating that
for a second scorer would mean two places to fix the next one.
"""

import anthropic
import httpx2
import pytest

from jobhunt.discovery.models import Posting
from jobhunt.matching.models import CompanyVerdict, RoleVerdict
from jobhunt.matching.scorer import ScoreError, Scorer

POSTING_TEXT = ("Acme Robotics | Product Engineer | Remote US\n"
                "You will run usability testing with our field technicians and "
                "own the product experience end to end. Python, FastAPI.")


def posting(text=POSTING_TEXT):
    return Posting(id="hn:1", source="hn", source_thread_id="9",
                   company="Acme Robotics", company_normalised="acme robotics",
                   raw_text=text, apply_url=None, is_remote=True,
                   geo_signal="us", posted_at="2026-09-01", first_seen="x",
                   last_seen="x", status="new")


def role_verdict(**kw):
    base = dict(role_title="Product Engineer", discipline="engineering",
                reason="duties include usability testing", hybrid_test="pass", domain_fit="none",
                hybrid_evidence=["run usability testing with our field technicians"],
                overlaps=["usability testing"], gaps=[], role_fit="strong")
    return RoleVerdict(**{**base, **kw})


def company_verdict(**kw):
    base = dict(reason="builds field robotics", domain_pull="present",
                designs_how_things_work="present", design_valued="unknown",
                stage_fit="present", reachability="unknown", disqualifier=None,
                company_fit="target")
    return CompanyVerdict(**{**base, **kw})


class FakeBlock:
    def __init__(self, parsed): self.type = "text"; self.parsed_output = parsed
class FakeMsg:
    def __init__(self, parsed):
        self.content = [FakeBlock(parsed)]; self.stop_reason = "end_turn"
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5,
                                    "cache_read_input_tokens": 100})()
class FakeMessages:
    def __init__(self, parsed): self._parsed = parsed; self.kwargs = None
    def parse(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self._parsed, Exception):
            raise self._parsed
        return FakeMsg(self._parsed)
class FakeClient:
    def __init__(self, parsed): self.messages = FakeMessages(parsed)


def test_score_role_returns_a_role_verdict():
    s = Scorer(FakeClient(role_verdict()))
    assert s.score_role("SYSTEM", posting()).role_fit == "strong"


def test_score_company_returns_a_company_verdict():
    s = Scorer(FakeClient(company_verdict()))
    got = s.score_company("SYSTEM", "Acme Robotics", "they build robots")
    assert got.company_fit == "target"


def test_the_system_block_is_cached_on_both_scorers():
    """~5K tokens, identical for every call in a run. Without cache_control
    the role axis alone pays full price 132 times."""
    c = FakeClient(role_verdict())
    Scorer(c).score_role("SYSTEM", posting())
    assert c.messages.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}

    c2 = FakeClient(company_verdict())
    Scorer(c2).score_company("SYSTEM", "Acme", "text")
    assert c2.messages.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_the_company_call_never_sees_the_posting_id_or_the_profile():
    c = FakeClient(company_verdict())
    Scorer(c).score_company("COMPANY SYSTEM", "Acme", "they build robots")
    sent = str(c.messages.kwargs["messages"])
    assert "hn:1" not in sent


def test_hybrid_evidence_must_appear_in_the_posting():
    """"Titles are NOT evidence" is only enforceable if the quoted duty is
    really there. A model that paraphrases, or invents, a qualifying duty
    defeats the Hybrid Test while appearing to satisfy it."""
    fabricated = role_verdict(
        hybrid_evidence=["you will lead our design system practice"])
    with pytest.raises(ScoreError, match="not found in the posting"):
        Scorer(FakeClient(fabricated)).score_role("SYSTEM", posting())


def test_hybrid_evidence_matching_ignores_case_and_whitespace():
    quoted = role_verdict(
        hybrid_evidence=["Run  USABILITY testing\n with our field technicians"])
    assert Scorer(FakeClient(quoted)).score_role("SYSTEM", posting()).hybrid_test == "pass"


def test_a_short_evidence_fragment_is_not_checked():
    """Below a sensible length a phrase matches by accident, so the check
    would be noise rather than a guard."""
    short = role_verdict(hybrid_test="weak", role_fit="partial",
                         hybrid_evidence=["design"])
    assert Scorer(FakeClient(short)).score_role("SYSTEM", posting()).role_fit == "partial"


def test_evidence_is_not_checked_when_the_hybrid_test_did_not_pass():
    v = role_verdict(hybrid_test="fail", role_fit="no", hybrid_evidence=[])
    assert Scorer(FakeClient(v)).score_role("SYSTEM", posting()).role_fit == "no"


def test_a_validation_error_is_wrapped_as_a_score_error():
    """messages.parse validates inside its post_parser, so a schema violation
    - including the hybrid gate - raises past every `except anthropic.*`."""
    import pydantic

    class Boom(pydantic.BaseModel):
        x: int
    try:
        Boom(x="no")
    except pydantic.ValidationError as exc:
        err = exc
    with pytest.raises(ScoreError, match="validation"):
        Scorer(FakeClient(err)).score_role("SYSTEM", posting())


def test_an_auth_failure_still_propagates_as_a_score_error():
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    exc = anthropic.AuthenticationError("bad key",
                                        response=httpx2.Response(401, request=req),
                                        body=None)
    with pytest.raises(ScoreError):
        Scorer(FakeClient(exc)).score_company("SYSTEM", "Acme", "text")
