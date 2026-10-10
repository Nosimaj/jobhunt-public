# src/jobhunt/matching/geography.py
"""Is this role reachable? Deterministic, pure, and never rendered into a rubric.

Geography is a FACT, not a judgement. Asking a model whether Andover is
commutable from Boston pays for a lookup, and a model is wrong about commuting
in ways a table is not. This keeps the 2026-09-29 insight intact - the rubric
decides whether the JOB is right, this module decides whether they can TAKE it -
while making the radius free to re-tune: widening `home_states` costs no
re-score.

The default is KEEP. `unknown` is never `out_of_area`.
"""

from __future__ import annotations

import re
from typing import Literal

from .commute import _STATE_NAMES, is_commutable

Reach = Literal["remote", "local", "out_of_area", "unknown"]

#: A remote option ANYWHERE in the string wins. Live values put the remote
#: option last ("Dallas, TX | REMOTE (US) or HYBRID"), so a first-token parse
#: would discard a reachable role.
_REMOTE_RE = re.compile(r"\b(remote|virtual|anywhere|distributed|work from home|wfh)\b",
                        re.I)

#: A deliberately PERMISSIVE guard: its only job is to stop a string with no
#: alphabetic content at all ("--", "12", "me") from reading as a place. An
#: unrecognised word-like string - including a sentence such as "Tell me about
#: the role" - falls through to `out_of_area`. That is MEASURED, not
#: accidental: of 445 comma-less `out_of_area` locations in the live corpus,
#: ~443 are genuine cities ("San Francisco", "New York office", "Austin"), and
#: requiring a comma or a known city would flip them all to `unknown` and pay
#: to score every one. Do not "fix" this back to demanding place evidence.
_PLACE_RE = re.compile(r"[A-Za-z]{3,}")

#: Workplace-type and placeholder words that name no place. Insurance, not a
#: correction: almost no live row is affected. Stripped before the place
#: check, so "Hybrid" is `unknown` but "Hybrid, Austin" is still `out_of_area`.
_NON_PLACE_RE = re.compile(
    r"\b(hybrid|on-?site|in[- ]office|remote-first|tbd|n/a|multiple|field|"
    r"various|only|united states|usa|u\.s\.?|nationwide|north america)\b",
    re.I)


#: An office they must physically attend, named in the location text. This
#: OVERRIDES a board's `workplace_type: remote`, which is a claim the text
#: sometimes contradicts outright - one live row read "Work in person in our
#: office @ 425 2nd St" while the structured field said remote, and it landed
#: in the apply bucket. The field is a claim; the text is evidence.
_ONSITE_RE = re.compile(
    r"\b(work in person|in[-\s]person|on[-\s]?site|in[-\s]office)\b", re.I)

#: Regions that are remote but not remote FOR HIM. Case-SENSITIVE on the
#: abbreviations, per the project rule that `re.I` on an abbreviation is a bug
#: class - `\bUS\b` under re.I matched the pronoun "us" in 39 of 260 posts.
#: AMER and "North America" deliberately absent: they CONTAIN the home area.
_FOREIGN_REGION_RE = re.compile(
    r"\b(?-i:EMEA|APAC|LATAM)\b"
    r"|\b(asia[-\s]pacific|europe, middle east|latin america)\b", re.I)


#: An Americas/US token that VETOES the foreign-region rule. A posting open to
#: both AMER and EMEA is open to them; excluding it on the foreign token alone
#: is a false negative, and false negatives are the expensive error in this
#: project. Measured: this veto recovers 10 live rows, one of them a stored
#: `maybe_queue` ("FULLY REMOTE (US / Canada / Europe / UK / Brazil / LATAM)").
#: Abbreviations case-SENSITIVE, per the rule that `re.I` on an abbreviation is
#: a bug class here.
_AMERICAS_RE = re.compile(
    r"\b(?-i:AMER|NAMER|US|USA|U\.S\.)\b"
    r"|\b(north america|united states|americas|canada)\b", re.I)


def _in_home_state(text: str, states: list[str]) -> bool:
    for state in states:
        code = state.strip().upper()
        # CASE-SENSITIVE on the two-letter code. Under re.I, `\bME\b` matches
        # the pronoun "me" and `\bMA\b` matches "ma" - slice 2's gotcha 1,
        # which mislabelled 39 of 260 posts.
        if re.search(rf"\b{re.escape(code)}\b", text):
            return True
        full = _STATE_NAMES.get(code)
        # The NAME is safe under re.I and must be \b-anchored so that
        # `maine` cannot match inside `domain`.
        if full and re.search(rf"\b{re.escape(full)}\b", text, re.I):
            return True
    return False


def reachability(location: str | None, workplace_type: str | None,
                 metros: list[str], states: list[str]) -> Reach:
    """`remote` | `local` | `out_of_area` | `unknown`.

    `unknown` means "no usable location text", and it is KEPT, never excluded.
    """
    text = (location or "").strip()
    # A foreign-only region is remote, but not remote for someone in New
    # England. Checked BEFORE workplace_type, which only says "not at a desk
    # we own" and carries no geography at all.
    if _FOREIGN_REGION_RE.search(text) and not _AMERICAS_RE.search(text):
        return "out_of_area"
    # An explicit on-site location overrides `workplace_type: remote`. Only
    # when the place is outside the home area - a local office is exactly the
    # on-site role they DO want, so fall through to the normal checks.
    # ...but only when the text also NAMES a place. A bare "Onsite" or
    # "In-office" with no town is a workplace-type word, not a location, and
    # must stay `unknown` and therefore KEPT - the default is never to discard.
    if (_ONSITE_RE.search(text)
            and _PLACE_RE.search(_NON_PLACE_RE.sub(" ", text))
            and not (is_commutable(text, metros) or _in_home_state(text, states))):
        return "out_of_area"
    if (workplace_type or "").strip().casefold() == "remote":
        return "remote"
    if not text:
        return "unknown"
    if _REMOTE_RE.search(text):
        return "remote"
    if is_commutable(text, metros) or _in_home_state(text, states):
        return "local"
    if not _PLACE_RE.search(_NON_PLACE_RE.sub(" ", text)):
        return "unknown"
    return "out_of_area"
