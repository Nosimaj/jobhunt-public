"""Make "select and re-angle, never generate from scratch" mechanical.

Two checks, applied to every bullet the model returns. A fabricated metric on
a resume is the one failure here that can genuinely harm Sam; everything
else is a matter of taste.

Neither check applies to cover-letter prose - see spec section 4. A cover
letter legitimately names the company it is addressed to, so the proper-noun
rule would forbid the obvious.
"""

from __future__ import annotations

import re

#: The similarity floor. Deliberately permissive, to be tuned against real
#: output rather than guessed at. Named so tuning is a one-line change.
MIN_TOKEN_OVERLAP = 0.5

_NUMERAL_RE = re.compile(r"\d+(?:[.,]\d+)*")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z''\-]*")

#: Excluded from the overlap so two unrelated sentences do not look similar
#: merely for sharing English scaffolding.
_STOPWORDS = frozenset("""
a an and are as at be been by for from had has have he her his in into is it
its of on or our she that the their them they this to was were will with you
your i me my we us not but if then than so such can could would should may
""".split())


def new_numerals(original: str, rewritten: str) -> list[str]:
    """Numerals in the rewrite that are absent from the original."""
    had = set(_NUMERAL_RE.findall(original))
    return [n for n in _NUMERAL_RE.findall(rewritten) if n not in had]


def _proper_nouns(text: str) -> set[str]:
    """Capitalised words, ignoring sentence-initial position.

    A capital at the start of a sentence is grammar, not a proper noun; if it
    counted, every re-angle that opens with a different word would be refused.
    """
    out: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        words = _WORD_RE.findall(sentence)
        for word in words[1:]:                      # skip sentence-initial
            if word[0].isupper():
                out.add(word)
    return out


def new_proper_nouns(original: str, rewritten: str) -> list[str]:
    had = {w.casefold() for w in _WORD_RE.findall(original)}
    return sorted(w for w in _proper_nouns(rewritten) if w.casefold() not in had)


def _content_words(text: str) -> set[str]:
    return {w.casefold() for w in _WORD_RE.findall(text)
            if w.casefold() not in _STOPWORDS}


def token_overlap(original: str, rewritten: str) -> float:
    """Fraction of the ORIGINAL's content words retained by the rewrite.

    Keyed on the original, not on a symmetric measure: the question is how
    much of their prose survived, not how similar the two strings are.
    """
    had = _content_words(original)
    if not had:
        return 1.0
    return len(had & _content_words(rewritten)) / len(had)


def check_reangle(original: str, rewritten: str) -> str | None:
    """None when the rewrite is an acceptable re-angle, else the reason.

    The caller uses the ORIGINAL when this returns a reason - a refused
    bullet is never silently corrected, and never dropped.
    """
    numerals = new_numerals(original, rewritten)
    if numerals:
        return (f"invented numeral(s) {', '.join(numerals)} absent from the "
                "original")
    nouns = new_proper_nouns(original, rewritten)
    if nouns:
        return (f"invented proper noun(s) {', '.join(nouns)} absent from the "
                "original")
    overlap = token_overlap(original, rewritten)
    if overlap < MIN_TOKEN_OVERLAP:
        return (f"token overlap {overlap:.2f} is below MIN_TOKEN_OVERLAP "
                f"{MIN_TOKEN_OVERLAP} - this is generation, not a re-angle")
    return None
