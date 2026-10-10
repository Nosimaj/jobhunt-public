"""Extract an ATS board token from a job advertisement. Pure.

Measured 2026-09-28: 42 of 260 HN posts (18%) leak one, and all 42 boards
fetched successfully. This registry is the only thing in the project that
names companies, so a missed token is a company never polled again.
"""

from __future__ import annotations

import re

#: Precedence when a post links more than one board. Stated rather than left
#: to dict iteration order, because "whichever we happened to see first" is
#: not a decision. Ashby first: it is both the most common in the data (32 of
#: 42) and the richest response (structured isRemote, workplaceType,
#: compensation), so it is the better board to poll when there is a choice. Workable and
#: Teamtailor come last: rarest in the data, so they only win by default.
PROVIDER_ORDER: tuple[str, ...] = ("ashby", "greenhouse", "lever", "workable",
                                  "teamtailor")

#: A token is [a-z0-9_-]+. The trailing (?![\w-]) stops a match from eating a
#: following path segment, and keeps a sentence-final full stop out of it.
_PATTERNS: dict[str, re.Pattern] = {
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)(?![\w-])"),
    "greenhouse": re.compile(
        r"(?:boards|job-boards)\.greenhouse\.io/"
        r"(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)(?![\w-])"),
    "lever": re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)(?![\w-])"),
    "workable": re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)(?![\w-])"),
    # Teamtailor puts the token in the SUBDOMAIN and posts write it bare
    # ("apply: axmed.teamtailor.com"), so no scheme is required. Instead the
    # token must start a hostname (no preceding word char or dot, so
    # `careers.na.teamtailor.com` yields nothing) and be 3+ chars: a 2-char
    # `na.teamtailor.com` in prose is not a board and would be polled, and
    # fail, every run.
    "teamtailor": re.compile(
        r"(?<![\w.-])([A-Za-z0-9][A-Za-z0-9-]{2,})\.teamtailor\.com(?![\w-])"),
}


def extract(text: str) -> tuple[str, str] | None:
    for provider in PROVIDER_ORDER:
        match = _PATTERNS[provider].search(text or "")
        if match:
            # Lowercased so the registry key is stable: the same board appears
            # as `Runway-ML` and `runway-ml` across two posts.
            return provider, match.group(1).casefold()
    return None


def harvest(store) -> int:
    """Write a token for every posting that carries one. Returns how many
    companies now have a board.

    Runs over EVERY posting, including rejected ones. The three best design
    roles in the September thread were at companies whose HN post was rejected
    `not_remote` - Smarkets, Baton, Tandem Health - and it is their BOARDS
    that carry the roles this slice exists to find.
    """
    found = 0
    for posting in store.all_postings():
        hit = extract(posting.raw_text)
        if hit is None:
            continue
        store.set_ats_token(posting.company_normalised, posting.company, *hit)
        found += 1
    return found
