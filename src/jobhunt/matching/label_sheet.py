"""Generate a blind labelling sheet for the two-axis eval.

Blind labelling was the single highest-leverage step in slice 3 - 25 postings
marked before any prompt existed, which rewrote the design twice. Nothing in
the generated sheet may reveal a model's opinion, or the human is anchored and
the ground truth is gone.

WHY THE SAMPLE IS ENRICHED. The first sample was uniformly random and returned
1 strong / 4 maybe / 20 no. Reaching the ~20 non-`no` labels the amendment asks
for by random sampling would mean hand-labelling roughly a hundred more
postings. So this deliberately over-samples postings that carry a design or
hybrid signal, and adds a smaller seeded random slice so the sheet still
contains true negatives.

The cost of that choice, which must not be forgotten: **an enriched sample can
measure AGREEMENT but cannot estimate base rates.** The 4% strong rate comes
from the original random sample and only from there.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

#: Keyed on TITLES and DUTIES only.
#:
#: A domain-keyword filter was tried and abandoned: it matched 66 of 107
#: postings, because "mental health support" in a benefits list scores as the
#: health domain. FlixTrain - a rail company - matched on its perks. That is
#: the same failure as slice 2's `\\bUS\\b` matching the pronoun "us", and the
#: lesson is the same: match on the part of the document that carries the
#: meaning, not on any occurrence of the word.
ROLE_SIGNALS: dict[str, str] = {
    "hybrid_title": (r"design engineer|product engineer|design technologist"
                     r"|creative engineer|prototyp\w* engineer"),
    "design_duty": (r"design system|usability test|user research"
                    r"|interaction design|user experience|\bux\b|figma"
                    r"|wireframe|prototyp"),
    "design_role": (r"\bproduct designer|\bux designer|\bdesigner\b"
                    r"|head of design|design lead"),
    "hybrid_phrase": (r"design and code|designs? and build|design-minded"
                      r"|product-minded|designer who codes|both design and"),
}

#: Sections whose text describes the EMPLOYER's perks rather than the job, and
#: which therefore must not contribute a signal.
_PERKS_RE = re.compile(
    r"(perks?|benefits?|we offer|what we offer)\b.*", re.I | re.S)


def _judgeable_text(raw: str) -> str:
    """The posting with its perks tail removed."""
    return _PERKS_RE.sub("", raw)


def signals_for(posting) -> list[str]:
    text = _judgeable_text(posting.raw_text).casefold()
    return [name for name, rx in ROLE_SIGNALS.items() if re.search(rx, text)]


@dataclass
class Selection:
    enriched: list = field(default_factory=list)
    random: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)

    @property
    def all(self) -> list:
        return self.enriched + self.random


def select(pool, *, seed: int, random_n: int,
           exclude: set[str] | None = None) -> Selection:
    """Every posting carrying a role signal, plus `random_n` others.

    The random slice is seeded and drawn from an id-sorted list, so the sample
    is reproducible and defensible later - the property the first eval set
    records its own seed for.
    """
    exclude = exclude or set()
    candidates = sorted((p for p in pool if p.id not in exclude),
                        key=lambda p: p.id)
    sel = Selection()
    for posting in candidates:
        found = signals_for(posting)
        if found:
            sel.enriched.append(posting)
            sel.signals[posting.id] = found
    chosen_ids = {p.id for p in sel.enriched}
    rest = [p for p in candidates if p.id not in chosen_ids]
    rng = random.Random(seed)
    rng.shuffle(rest)
    sel.random = rest[:random_n]
    return sel


_HEADER = """\
# Two-axis labelling sheet — {total} postings

Seed `{seed}`. **{n_enriched} enriched** (they carry a design or hybrid signal)
and **{n_random} drawn at random** from the rest.

⚠️ **This sample is deliberately enriched, so it cannot be used to estimate
base rates.** It over-samples likely positives on purpose, because a uniformly
random sample returned only 1 strong and 4 maybes out of 25. Use it to measure
agreement. The real 4% strong rate comes from the original random sample and
only from there.

Mark **both axes** for each posting. Gut reaction is what's wanted.

**Role** — is this an opening they could win and would want?
- `S` strong — right discipline, or duties clearly want both design and code
- `P` partial — right discipline with one real gap, or only a weak hybrid signal
- `N` no — wrong discipline, or a hard requirement they can't meet

**Company** — would they want a conversation here, *ignoring the opening*?
- `T` target — pulls them in on domain, and it builds something real
- `U` unsure — nothing off-putting, but nothing pulling either
- `N` no — disqualifying, or no evidence it values design at all

The interesting cell is **Role N + Company T**: wrong job, right company. That
is the outreach lead the single-axis version had nowhere to put.

---
"""


def build_sheet(selection: Selection, *, seed: int) -> str:
    out = [_HEADER.format(total=len(selection.all), seed=seed,
                          n_enriched=len(selection.enriched),
                          n_random=len(selection.random))]
    for i, posting in enumerate(selection.all, start=1):
        why = selection.signals.get(posting.id)
        tag = f" · signal: `{', '.join(why)}`" if why else " · random draw"
        out.append(f"\n## {i}. {posting.company}\n"
                   f"`{posting.id}`{tag}\n\n"
                   f"```\n{posting.raw_text.strip()}\n```\n\n"
                   f"**Role:** ____   **Company:** ____\n\n"
                   f"*Why (one rule, not deliberation):* ____\n\n---")
    return "\n".join(out)
