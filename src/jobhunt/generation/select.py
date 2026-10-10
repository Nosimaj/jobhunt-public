"""Which roles render, which bullets are eligible, under which lens. Pure.

The audience firewall is enforced here: profile.json holds 90 bullets and only
27 may reach a document. The rest are unreviewed LinkedIn exporter prose.
"""

from __future__ import annotations

from .case_studies import CaseStudy

#: role_discipline (from the stored RoleVerdict) -> profile lens.
#: A two-way branch does not need a model call, and determinism means the same
#: posting always produces the same lens.
_LENS = {
    "design": "design",
    "design_engineering": "eng",
    "engineering": "eng",
    "product": "design",
    "other": "design",
}


def lens_for(role_discipline: str) -> str:
    return _LENS.get(role_discipline, "design")


def _flat(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def eligible_bullets(role: dict, lens: str) -> list[dict]:
    """Bullets that may appear in a document for this role under this lens.

    Four conditions, all of them firewall:
      - the role is not `minimal` (which renders header-only) or `omit`
      - the bullet is lens-tagged `both` or matches the lens
      - the bullet is `user_approved`
      - the bullet is not forbidden by the role's `do_not_include`
    """
    if role.get("render", "feature") != "feature":
        return []
    forbidden = _flat(role.get("do_not_include") or "")
    out = []
    for b in role.get("bullets", []):
        if b["audience"] not in ("both", lens):
            continue
        if not b.get("user_approved"):
            continue
        if forbidden and forbidden in _flat(b["text"]):
            continue
        out.append(b)
    return out


def renderable_roles(profile: dict) -> list[dict]:
    """Every role that appears in a document at all.

    `minimal` roles ARE renderable - they print employer, title and dates -
    they simply contribute no bullets. Only `omit` disappears.
    """
    return [r for r in profile["roles"] if r.get("render", "feature") != "omit"]


def relevant_case_studies(studies: list[CaseStudy], lens: str,
                          employers_used: set[str]) -> list[CaseStudy]:
    """Case studies citable for this lens, whose employer the document shows.

    Citing work at a company the resume does not display invites a question
    the document cannot answer. Independent work (no `evidences`) is always
    citable, since it can never be orphaned.
    """
    return [c for c in studies
            if c.lens in ("both", lens)
            and (c.evidences is None or c.evidences in employers_used)]
