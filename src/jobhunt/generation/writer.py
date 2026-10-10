"""The only code in slice 4 that calls the model.

Mirrors `matching/scorer.py`: one structured call behind an injected client,
with the same reasons - so no test makes a live call and every shape is
exercised against a fixture.
"""

from __future__ import annotations

import anthropic
import pydantic

from .guards import check_reangle
from .models import (AcceptedBullet, CoverLetter, ResumeSelection)

DEFAULT_MODEL = "claude-opus-5"
#: Generous: a resume selection plus reasoning runs longer than a verdict, and
#: adaptive thinking counts against this budget.
DEFAULT_MAX_TOKENS = 8000
DEFAULT_EFFORT = "medium"


class WriteError(RuntimeError):
    """The document could not be produced."""


class Writer:
    def __init__(self, client, *, model: str = DEFAULT_MODEL,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 effort: str = DEFAULT_EFFORT) -> None:
        self._client = client
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort

    def _call(self, system: str, user: str, output_format, label: str):
        try:
            msg = self._client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                # The profile is identical across both calls for one
                # application, so it is written once and read at a tenth.
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=output_format,
                output_config={"effort": self.effort},
            )
        except pydantic.ValidationError as exc:
            raise WriteError(f"{label}: response failed validation - {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise WriteError(f"{label}: API error - {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise WriteError(f"{label}: connection error - {exc}") from exc
        if getattr(msg, "stop_reason", None) == "max_tokens":
            raise WriteError(
                f"{label}: hit max_tokens ({self.max_tokens}) before finishing "
                "- thinking tokens count against this budget")
        for block in getattr(msg, "content", []) or []:
            parsed = getattr(block, "parsed_output", None)
            if parsed is not None:
                return parsed
        raise WriteError(f"{label}: response contained no parsed output")

    def select_bullets(self, system: str, user: str) -> ResumeSelection:
        return self._call(system, user, ResumeSelection, "resume selection")

    def write_cover_letter(self, system: str, user: str) -> CoverLetter:
        return self._call(system, user, CoverLetter, "cover letter")


def accept_selection(selection: ResumeSelection,
                     eligible_by_id: dict[str, str]) -> list[AcceptedBullet]:
    """Apply the guards. A refused re-angle falls back to the original.

    An id outside the eligible set is DROPPED, not repaired: the eligible set
    is the audience firewall, and an unknown id means the model either reached
    for an unreviewed bullet or invented one. Either way there is no original
    to fall back to.
    """
    out: list[AcceptedBullet] = []
    seen: set[str] = set()
    for chosen in selection.bullets:
        original = eligible_by_id.get(chosen.bullet_id)
        if original is None or chosen.bullet_id in seen:
            continue
        seen.add(chosen.bullet_id)
        reason = check_reangle(original, chosen.rewritten)
        out.append(AcceptedBullet(
            bullet_id=chosen.bullet_id,
            text=original if reason else chosen.rewritten,
            refused_reason=reason))
    return out


def check_citations(letter: CoverLetter, allowed_titles: set[str]) -> list[str]:
    """Titles the letter cites that were never offered to it."""
    return [t for t in letter.cited_case_studies if t not in allowed_titles]
