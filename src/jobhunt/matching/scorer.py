from __future__ import annotations

import anthropic
import pydantic

from .models import CompanyVerdict, MatchVerdict, RoleVerdict

DEFAULT_MODEL = "claude-opus-5"

#: Adaptive thinking is ON BY DEFAULT on Opus 5 and its tokens count against
#: `max_tokens`. The structured verdict itself is only a few hundred tokens,
#: but a rubric-heavy judgement can spend well past 2,000 thinking first - and
#: a `max_tokens` stop returns NO parsed output, so the budget has to cover
#: thinking + output or every posting fails. Output is billed on what is
#: actually generated, so the headroom is free.
DEFAULT_MAX_TOKENS = 8000

#: Screening 132 postings against a fixed rubric does not need `high` (the
#: Opus 5 default). `medium` is the cheapest quality lever available.
DEFAULT_EFFORT = "medium"


class ScoreError(RuntimeError):
    """One posting could not be scored. Never fails the whole run."""


class Scorer:
    """Scores one posting per call, with the profile cached as a system prefix.

    One call per posting is deliberate. Batching several postings would be
    cheaper, but a model asked to score a list ranks WITHIN the list, which
    would make a posting's bucket depend on which others shared its batch.
    Buckets must be stable.
    """

    def __init__(self, client, *, model: str = DEFAULT_MODEL,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 effort: str = DEFAULT_EFFORT) -> None:
        self._client = client
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.last_cache_read_tokens: int | None = None
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    def _call(self, system: str, user_text: str, output_format, label: str):
        """One structured call. Every scorer goes through here.

        The error handling below encodes several failure modes that cost real
        debugging to find - truncation arriving as a ValidationError rather
        than a max_tokens stop, refusals, the retryable/non-retryable split.
        A second scorer with its own copy would mean two places to fix the
        next one.
        """
        try:
            msg = self._client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                # cache_control on the system block: it is ~4.2K tokens and
                # identical for every posting in a run, so it is written once
                # and read at a tenth of the price.
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_text}],
                output_format=output_format,
                # parse() merges output_format in as output_config["format"],
                # so passing effort here does not displace the schema.
                output_config={"effort": self.effort},
            )
        # Most specific first: a broad `except APIStatusError` would lose the
        # retryable/non-retryable distinction.
        except anthropic.NotFoundError as exc:
            raise ScoreError(f"{label}: model not found - {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise ScoreError(f"{label}: rate limited - {exc}") from exc
        except anthropic.BadRequestError as exc:
            raise ScoreError(f"{label}: bad request - {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ScoreError(f"{label}: API status error - {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise ScoreError(f"{label}: connection error - {exc}") from exc
        except pydantic.ValidationError as exc:
            # `messages.parse` validates through a TypeAdapter inside its
            # post_parser, so a response that breaks the schema - most
            # importantly hybrid_signal 'none' with bucket 'strong', the
            # hybrid gate - raises here and NOT as an anthropic error. Without
            # this clause it escapes `ScoreError` entirely, contradicting this
            # method's contract with the CLI.
            #
            # This clause also fires BEFORE the `stop_reason` check below can
            # run, because validation happens inside the call: a response
            # truncated at `max_tokens` arrives as invalid JSON, not as a
            # readable message with stop_reason == "max_tokens". So the
            # truncation diagnosis has to be made from the error itself, or
            # the most likely first-real-run failure is reported as a schema
            # violation the model did not commit.
            if any(e.get("type") == "json_invalid" for e in exc.errors()):
                raise ScoreError(
                    f"{label}: the response was cut off mid-JSON, which "
                    f"almost always means it hit max_tokens ({self.max_tokens}) "
                    "- thinking tokens count against that budget. Raise "
                    "max_tokens or lower effort. Underlying error: "
                    f"{exc}") from exc
            raise ScoreError(
                f"{label}: response failed validation - {exc}") from exc

        usage = getattr(msg, "usage", None)
        self.last_cache_read_tokens = getattr(usage, "cache_read_input_tokens", None)
        self.last_input_tokens = getattr(usage, "input_tokens", None)
        self.last_output_tokens = getattr(usage, "output_tokens", None)

        # Always check stop_reason before reading content.
        stop_reason = getattr(msg, "stop_reason", None)
        if stop_reason == "refusal":
            raise ScoreError(
                f"{label}: model returned stop_reason 'refusal' "
                f"({getattr(msg, 'stop_details', None)})")
        if stop_reason == "max_tokens":
            # Reached only when the budget was spent during THINKING, with no
            # verdict text emitted at all - a truncated verdict raises
            # `ValidationError` inside `parse` and is diagnosed there instead.
            # Without this branch that case falls through to the content loop
            # and is reported as "no parsed output": the wrong cause.
            raise ScoreError(
                f"{label}: hit max_tokens ({self.max_tokens}) before the "
                "verdict was complete - thinking tokens count against this "
                "budget; raise max_tokens or lower effort")

        for block in getattr(msg, "content", []) or []:
            parsed = getattr(block, "parsed_output", None)
            if parsed is not None:
                return parsed
        raise ScoreError(f"{label}: response contained no parsed output")

    # ---- the three entry points ----

    def score(self, system: str, posting) -> MatchVerdict:
        """The original single-axis verdict. Unchanged behaviour."""
        from .prompt import build_user
        return self._call(system, build_user(posting), MatchVerdict, posting.id)

    def score_role(self, system: str, posting) -> RoleVerdict:
        from .rubrics import build_role_user
        verdict = self._call(system, build_role_user(posting), RoleVerdict,
                             posting.id)
        _check_hybrid_evidence(verdict, posting)
        return verdict

    def score_company(self, system: str, company: str,
                      source_text: str) -> CompanyVerdict:
        """Scored per company, not per posting.

        `source_text` is injected rather than read here so the source can grow
        from "this company's postings" to a fetched careers page without
        touching the scorer.
        """
        from .rubrics import build_company_user
        return self._call(system, build_company_user(company, source_text),
                          CompanyVerdict, company)


#: Below this length a quoted fragment matches the posting by coincidence, so
#: checking it would produce noise rather than a guard.
EVIDENCE_MIN_LEN = 15


def _flat(text: str) -> str:
    return " ".join(text.split()).casefold()


def _check_hybrid_evidence(verdict: RoleVerdict, posting) -> None:
    """Every quoted duty must really be in the posting.

    "Titles are NOT evidence" is only enforceable if the duty the model
    quotes is actually there. A model that paraphrases a responsibility, or
    invents one, satisfies the Hybrid Test on paper while defeating it in
    fact - and that is the single cheapest way for the core steering rule to
    stop working without anyone noticing.

    Only checked on a `pass`, because that is the verdict the evidence
    licenses.
    """
    if verdict.hybrid_test != "pass":
        return
    haystack = _flat(posting.raw_text)
    for phrase in verdict.hybrid_evidence:
        needle = _flat(phrase)
        if len(needle) < EVIDENCE_MIN_LEN:
            continue
        if needle not in haystack:
            raise ScoreError(
                f"{posting.id}: hybrid_evidence {phrase!r} is not found in the "
                "posting - a paraphrased or invented duty defeats the Hybrid "
                "Test while appearing to satisfy it")
