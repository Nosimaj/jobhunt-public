from __future__ import annotations

import re

# Legal-form suffixes carry no identity information.
_SUFFIXES = {"inc", "inc.", "incorporated", "corp", "corp.", "corporation",
             "llc", "l.l.c.", "ltd", "ltd.", "limited", "co", "co.", "company",
             "group", "the"}

# Words the portfolio adds descriptively that the LinkedIn record omits.
# "Spokewell Bike Repair" vs "Spokewell Repair".
#
# Keep this set to words with a demonstrated, tested employer pair behind
# them. "assurance" was here for a company that is a manual_role and never
# reaches normalize_company at all -- a general rule invented for one string.
_FILLER = {"bike"}

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), " ")


def normalize_company(name: str) -> str:
    """Casefold, strip punctuation and legal suffixes, collapse whitespace."""
    text = name.translate(_DASHES).replace("/", " ").replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    tokens = [t for t in text.split() if t and t not in _SUFFIXES and t not in _FILLER]
    return " ".join(tokens)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug


class AliasTable:
    """Maps any known alias of an employer to its canonical name."""

    def __init__(self, aliases: list[dict] | None = None) -> None:
        self._by_norm: dict[str, str] = {}
        for entry in aliases or []:
            canonical = entry["canonical"]
            names = [canonical, *entry.get("aliases", [])]
            for name in names:
                self._by_norm[normalize_company(name)] = canonical

    def canonical(self, name: str) -> str:
        return self._by_norm.get(normalize_company(name), name)
