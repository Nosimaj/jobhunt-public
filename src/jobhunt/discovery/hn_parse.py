from __future__ import annotations

import html
import re

from .models import GeoSignal, HnRawPost, ParsedPost

REMOTE_RE = re.compile(r"\bremote\b", re.I)

# A US marker means the role is open to US applicants. "worldwide" and
# "anywhere" count, since they include the US.
#
# Country/timezone abbreviations: ALWAYS uppercase in practice. Case-SENSITIVE,
# because re.I makes \bUS\b match the English pronoun "us" ("email us", "join us")
# and \bET\b match "et al" - which mislabelled 39 of 152 posts and let four
# explicitly EU-only roles through as candidates.
US_ABBREV_RE = re.compile(r"\bUS\b|\bU\.S\.|\bUSA\b|\bET\b|\bPT\b|\bEST\b|\bPST\b")
# Word forms: safe to match case-insensitively.
US_WORDS_RE = re.compile(r"United States|Americas|worldwide|anywhere", re.I)

EU_RE = re.compile(
    r"remote\s*\(?(europe|eu\b|emea|uk\b)|europe only|eu[- ]based|emea\b"
    r"|\bCET\b|\bCEST\b", re.I)

ROLE_RE = re.compile(
    r"engineer|developer|design|product|founding|full.?stack|front.?end"
    r"|back.?end|technical", re.I)

_URL_RE = re.compile(r"https?://\S+")
# Matches a URL plus any wrapping parentheses, for stripping out of a company name.
_URL_IN_COMPANY_RE = re.compile(r"\(?\s*https?://\S+?\s*\)?(?=\s|$|\|)")
_COMPANY_MAX = 60

#: Padding and trailing punctuation to trim off a company segment. Brackets
#: are handled separately, by `_strip_edges`.
_EDGE_PUNCT = " \t-–—:,;"
_BRACKETS = "()[]"


def _brackets_balanced(text: str) -> bool:
    return (text.count("(") == text.count(")")
            and text.count("[") == text.count("]"))


def _strip_edges(text: str) -> str:
    """Trim padding and edge punctuation from a company segment.

    A bracket is removed only when the remainder stays balanced. The pipe
    split orphans the closing paren of "Monumint (YC W24) | Remote", and a
    blanket `.strip("()[]")` then removed it, leaving "Monumint (YC W24" -
    an unbalanced bracket on 15 of 260 companies.
    """
    while True:
        trimmed = text.strip(_EDGE_PUNCT)
        if trimmed and trimmed[-1] in _BRACKETS and _brackets_balanced(trimmed[:-1]):
            trimmed = trimmed[:-1]
        elif trimmed and trimmed[0] in _BRACKETS and _brackets_balanced(trimmed[1:]):
            trimmed = trimmed[1:]
        if trimmed == text:
            return trimmed
        text = trimmed


def clean_post_text(html_text: str) -> str:
    """HN markup to plain text.

    HN escapes entities, so unescape must run before any regex - a URL
    arrives as `https:&#x2F;&#x2F;example.com`, and an early extraction
    attempt found zero ATS URLs in a thread visibly containing 54.

    Unescape runs TWICE, but for the /items/<id> endpoint the second pass is
    a NO-OP and is kept only as defensive insurance. Measured on the
    committed fixture: 254 of 260 posts contain `&#x2F;` and ZERO contain
    `&amp;#x2F;`, and one pass already removes every `&#x2F;`. The double
    encoding was observed on the SEARCH endpoint's `comment_text` field, not
    here. The second pass is not idempotent in one edge case: a literal
    `&amp;lt;div&amp;gt;` becomes `<div>` and is then deleted by the tag
    stripper. No occurrence in the September thread.

    Paragraph breaks become newlines and are preserved, because paragraph
    structure carries meaning for slice 3; runs of spaces and tabs collapse.
    """
    text = html.unescape(html.unescape(html_text))
    text = re.sub(r"</p\s*>", "", text, flags=re.I)
    text = re.sub(r"<p\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def is_remote(text: str) -> bool:
    return bool(REMOTE_RE.search(text))


def geo_signal_of(text: str) -> GeoSignal:
    """us when a US marker is present; eu_only when an EU marker is present and
    a US marker is not; unknown otherwise.

    A US marker wins over an EU marker: "Remote EMEA or US" is open to the US.
    """
    if US_ABBREV_RE.search(text) or US_WORDS_RE.search(text):
        return "us"
    if EU_RE.search(text):
        return "eu_only"
    return "unknown"


def company_of(cleaned: str) -> str:
    """The first pipe-delimited segment of the first line.

    Only the first segment is dependable; everything after it appears in an
    inconsistent order (spec section 3.3). With no pipe, fall back to a
    truncation of the first line - messy but honest, and `raw_text` carries
    the truth for slice 3.
    """
    first = cleaned.split("\n")[0].strip()
    segment = first.split("|")[0] if "|" in first else first[:_COMPANY_MAX]
    segment = _URL_IN_COMPANY_RE.sub(" ", segment)
    segment = re.sub(r"\s{2,}", " ", segment)
    return _strip_edges(segment)


def first_url_of(cleaned: str) -> str | None:
    match = _URL_RE.search(cleaned)
    if not match:
        return None
    return match.group(0).rstrip(").,;]")


def parse_post(raw: HnRawPost) -> ParsedPost:
    cleaned = clean_post_text(raw.text)
    if not cleaned:
        raise ValueError(f"post {raw.item_id} has empty text after cleaning")
    company = company_of(cleaned)
    if not company:
        raise ValueError(f"post {raw.item_id} yielded an empty company")
    return ParsedPost(
        id=f"hn:{raw.item_id}",
        company=company,
        apply_url=first_url_of(cleaned),
        is_remote=is_remote(cleaned),
        geo_signal=geo_signal_of(cleaned),
        raw_text=cleaned,
    )
