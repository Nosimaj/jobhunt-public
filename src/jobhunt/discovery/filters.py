from __future__ import annotations

from pydantic import Field

from ..models import Strict
from .hn_parse import ROLE_RE
from .models import ParsedPost, RejectReason


class FilterResult(Strict):
    candidates: list[ParsedPost] = Field(default_factory=list)
    rejections: list[tuple[ParsedPost, RejectReason]] = Field(default_factory=list)


def classify(post: ParsedPost) -> RejectReason | None:
    """None means candidate.

    Deliberately permissive: reading every candidate in a month costs slice 3
    roughly 45K tokens, so a rule that occasionally passes a dud is far cheaper
    than one that silently discards a good role. Seniority, company size and
    tech stack are NOT filtered here - slice 3 judges those.

    Order matters: not_remote is checked first because it is the more
    fundamental disqualifier.
    """
    if not post.is_remote:
        return "not_remote"
    if post.geo_signal == "eu_only":
        return "eu_only"
    if not ROLE_RE.search(post.raw_text):
        return "no_role_signal"
    return None


def split(posts: list[ParsedPost]) -> FilterResult:
    result = FilterResult()
    for post in posts:
        reason = classify(post)
        if reason is None:
            result.candidates.append(post)
        else:
            result.rejections.append((post, reason))
    return result
