import anthropic
import pytest

from jobhunt.discovery.models import Posting
from jobhunt.matching.models import MatchVerdict
from jobhunt.matching.scorer import ScoreError, Scorer


def posting():
    return Posting(id="hn:1", source="hn", source_thread_id="9", company="Acme",
                   company_normalised="acme", raw_text="Acme | Design Engineer | Remote US",
                   apply_url=None, is_remote=True, geo_signal="us",
                   posted_at="2026-09-01", first_seen="x", last_seen="x", status="new")


def verdict(**kw):
    base = dict(role_title="Design Engineer", discipline="design",
                hybrid_signal="explicit", reason="r", concern="a four-person team may not have a design practice",
                fit_score=80, bucket="strong")
    return MatchVerdict(**{**base, **kw})


class FakeBlock:
    def __init__(self, parsed): self.type = "text"; self.parsed_output = parsed


class FakeMessage:
    def __init__(self, parsed, stop_reason="end_turn", cache_read=1234):
        self.content = [FakeBlock(parsed)]
        self.stop_reason = stop_reason
        self.stop_details = None
        self.usage = type("U", (), {"cache_read_input_tokens": cache_read,
                                    "input_tokens": 50, "output_tokens": 120})()


class FakeMessages:
    def __init__(self, result): self._result = result; self.calls = []
    def parse(self, **kw):
        self.calls.append(kw)
        if isinstance(self._result, Exception): raise self._result
        return self._result


class FakeClient:
    def __init__(self, result): self.messages = FakeMessages(result)


def test_returns_the_parsed_verdict():
    c = FakeClient(FakeMessage(verdict()))
    got = Scorer(c).score("SYSTEM", posting())
    assert got.bucket == "strong" and got.role_title == "Design Engineer"


def test_system_block_is_sent_with_cache_control():
    c = FakeClient(FakeMessage(verdict()))
    Scorer(c).score("SYSTEM TEXT", posting())
    kw = c.messages.calls[0]
    assert kw["system"][0]["text"] == "SYSTEM TEXT"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_output_format_is_the_pydantic_model():
    c = FakeClient(FakeMessage(verdict()))
    Scorer(c).score("S", posting())
    assert c.messages.calls[0]["output_format"] is MatchVerdict


def test_no_temperature_betas_or_fallbacks_are_sent():
    # Verified against anthropic 1.8.0: parse() accepts none of these.
    c = FakeClient(FakeMessage(verdict()))
    Scorer(c).score("S", posting())
    kw = c.messages.calls[0]
    for forbidden in ("temperature", "betas", "fallbacks"):
        assert forbidden not in kw


def test_model_and_max_tokens_are_passed():
    c = FakeClient(FakeMessage(verdict()))
    Scorer(c, model="claude-opus-5", max_tokens=2000).score("S", posting())
    kw = c.messages.calls[0]
    assert kw["model"] == "claude-opus-5" and kw["max_tokens"] == 2000


def test_user_message_contains_only_the_posting():
    c = FakeClient(FakeMessage(verdict()))
    Scorer(c).score("S", posting())
    msgs = c.messages.calls[0]["messages"]
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    assert "Acme | Design Engineer | Remote US" in msgs[0]["content"]


def test_refusal_is_raised_not_read_as_a_verdict():
    c = FakeClient(FakeMessage(verdict(), stop_reason="refusal"))
    with pytest.raises(ScoreError, match="refusal"):
        Scorer(c).score("S", posting())


def test_missing_parsed_output_raises():
    class Empty(FakeMessage):
        def __init__(self): super().__init__(None); self.content = []
    with pytest.raises(ScoreError, match="no parsed"):
        Scorer(FakeClient(Empty())).score("S", posting())


def test_api_error_is_wrapped_with_the_posting_id():
    err = anthropic.APIConnectionError(request=None)
    with pytest.raises(ScoreError, match="hn:1"):
        Scorer(FakeClient(err)).score("S", posting())


def test_cache_read_tokens_are_exposed_for_verification():
    c = FakeClient(FakeMessage(verdict(), cache_read=9999))
    s = Scorer(c); s.score("S", posting())
    assert s.last_cache_read_tokens == 9999


def test_effort_is_sent_alongside_the_output_format():
    # parse() merges output_format into output_config["format"], so effort must
    # not displace the schema.
    c = FakeClient(FakeMessage(verdict()))
    Scorer(c, effort="medium").score("SYSTEM", posting())
    kw = c.messages.calls[0]
    assert kw["output_config"]["effort"] == "medium"
    assert kw["output_format"] is MatchVerdict


def test_max_tokens_default_leaves_room_for_thinking():
    # Adaptive thinking is on by default on Opus 5 and its tokens count
    # against max_tokens. 2000 was the original budget and was not enough.
    from jobhunt.matching.scorer import DEFAULT_MAX_TOKENS
    assert DEFAULT_MAX_TOKENS >= 8000


def test_a_max_tokens_stop_during_thinking_is_reported_as_such():
    """Reached only when the budget was spent thinking and no verdict text was
    emitted. A truncated VERDICT cannot arrive this way - see the test below.
    Without this branch it falls through to the content loop and is reported
    as "no parsed output": the wrong cause."""
    c = FakeClient(FakeMessage(None, stop_reason="max_tokens"))
    with pytest.raises(ScoreError) as exc:
        Scorer(c).score("SYSTEM", posting())
    assert "max_tokens" in str(exc.value)
    assert "no parsed output" not in str(exc.value)


def test_a_truncated_verdict_is_diagnosed_as_truncation_not_a_schema_error():
    """The real shape of the likeliest first-real-run failure, and the reason
    the `stop_reason` branch above is not enough on its own.

    `messages.parse` validates INSIDE the call, so a verdict cut off at
    max_tokens arrives as a `ValidationError` on invalid JSON - the
    `stop_reason` check never runs. Diagnosed from the error itself, or the
    user is told the model broke a schema it did not break.
    """
    import pydantic
    ta = pydantic.TypeAdapter(MatchVerdict)
    try:
        ta.validate_json('{"role_title": "Design Engineer", "discipline": "des')
    except pydantic.ValidationError as e:
        truncated = e
    assert [x["type"] for x in truncated.errors()] == ["json_invalid"]

    with pytest.raises(ScoreError) as exc:
        Scorer(FakeClient(truncated)).score("SYSTEM", posting())
    assert "cut off mid-JSON" in str(exc.value)
    assert "max_tokens" in str(exc.value)


def test_a_schema_violation_is_wrapped_in_score_error():
    """`messages.parse` validates inside its post_parser, so a pydantic
    ValidationError - most importantly the hybrid gate rejecting
    none+strong - escapes every `except anthropic.*` clause. Unwrapped it
    breaks `score`'s contract with the CLI."""
    import pydantic
    try:
        MatchVerdict(role_title="X", discipline="design", hybrid_signal="none",
                     reason="r", concern="c" * 30, fit_score=90, bucket="strong")
    except pydantic.ValidationError as e:
        boom = e
    c = FakeClient(boom)
    with pytest.raises(ScoreError) as exc:
        Scorer(c).score("SYSTEM", posting())
    assert "failed validation" in str(exc.value)
