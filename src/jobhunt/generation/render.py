"""Selection -> markdown. Pure: no model, no filesystem."""

from __future__ import annotations

import re

from .case_studies import CaseStudy
from .models import AcceptedBullet, CoverLetter

from .deliver import plain_dashes


def slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (text or "").casefold()).strip("-")
    return out or "untitled"


def _year(stamp: str, precision: str) -> str:
    if (stamp or "").casefold() == "present":
        return "Present"
    return stamp[:4] if precision == "years" else stamp


def resume_markdown(profile: dict, roles: list[dict],
                    accepted_by_role: dict[str, list[AcceptedBullet]],
                    lens: str) -> str:
    ident = profile["identity"]
    precision = profile.get("display_precision", "years")
    lines = [f"# {ident['name']}", "", ident["headline"], "",
             f"{ident['location']} · {ident['email']}"]
    links = " · ".join(f"[{k}]({v})" for k, v in (ident.get("links") or {}).items())
    if links:
        lines += ["", links]
    lines += ["", "## Summary", "", profile["summaries"][lens], "", "## Experience"]

    for role in sorted(roles, key=lambda r: r["start"], reverse=True):
        span = f"{_year(role['start'], precision)} - {_year(role['end'], precision)}"
        lines += ["", f"### {role['title_presented']} · {role['company']}",
                  f"*{span}*"]
        # A `minimal` role prints this header and stops. Bullets are excluded
        # upstream by `select.eligible_bullets`, and excluded again here so a
        # caller that hands us bullets for a minimal role cannot leak them.
        if role.get("render", "feature") != "feature":
            continue
        for bullet in accepted_by_role.get(role["id"], []):
            lines.append(f"- {bullet.text}")

    skills = [s["text"] for s in profile["skills"]
              if s["audience"] in ("both", lens)]
    if skills:
        lines += ["", "## Skills", "", ", ".join(skills)]
    return plain_dashes("\n".join(lines) + "\n")


def cover_letter_markdown(identity: dict, company: str, role_title: str,
                          letter: CoverLetter,
                          studies: list[CaseStudy]) -> str:
    cited = {c.title: c for c in studies if c.title in letter.cited_case_studies}
    lines = [f"# {role_title} - {company}", "", "Dear Hiring Team,", ""]
    lines += [p + "\n" for p in letter.paragraphs]
    if cited:
        lines += ["Relevant work:", ""]
        lines += [f"- [{c.title}]({c.url}) - {c.text}" for c in cited.values()]
        lines.append("")
    lines += ["Best regards,", "", identity["name"], identity["email"]]
    return plain_dashes("\n".join(lines) + "\n")


def posting_markdown(posting: dict, verdict: dict) -> str:
    # role_title lives on the VERDICT. `Posting` has no such field, so reading
    # it from `posting` produced the literal "Role" in every posting.md ever
    # written - and that string then flowed into application.json and into the
    # PDF filename. Same defect class as `p.role_title` in slice 5.
    role = verdict.get("role_title")
    return plain_dashes("\n".join([
        f"# {role or 'Role'} - {posting['company']}", "",
        # Company and role as EXPLICIT fields: the H1 is a display string, and
        # parsing it forced reader and writer to agree on a separator.
        f"- company: {posting['company']}",
        *([f"- role: {role}"] if role else []),
        f"- id: `{posting['id']}`",
        f"- location: {posting.get('location') or 'not stated'}",
        f"- apply: {posting.get('apply_url') or 'not stated'}",
        f"- role fit: {verdict.get('role_fit')} - {verdict.get('reason', '')}",
        "", "---", "", posting.get("raw_text", ""), ""]))
