"""The two prompts, as pure text. Readable and testable with no API key."""

from __future__ import annotations

from .case_studies import CaseStudy


def build_system(profile: dict, lens: str) -> str:
    """Shared across both calls for one application, so it is worth caching."""
    summary = profile["summaries"][lens]
    skills = ", ".join(s["text"] for s in profile["skills"]
                       if s["audience"] in ("both", lens))
    return f"""\
You are preparing a job application for the candidate. You do not invent
anything. Everything you write is selected from, or lightly re-angled out of,
material supplied below.

# Who they are, in this lens
{summary}

# Skills
{skills}

# The rules that matter
- Never introduce a number, a company name, a product name or a claim that is
  not already in the source material. A re-angled bullet that adds one is
  discarded and their original used instead, so inventing gains nothing.
- Keep their voice. These bullets are hand-written; you are choosing among them
  and adjusting emphasis, not rewriting them.
- Say no by omission: if a requirement has no evidence behind it, leave it
  out rather than stretching something to cover it.
"""


def build_bullet_user(posting_text: str, verdict: dict,
                      eligible: list[dict]) -> str:
    lines = [f"- [{b['id']}] {b['text']}" for b in eligible]
    overlaps = ", ".join(verdict.get("overlaps") or []) or "none recorded"
    return f"""\
Select the bullets for this application, in the order they should appear.

# The posting
{posting_text}

# What the screening step already found overlaps
{overlaps}

# ELIGIBLE bullets - you may use these ids and no others
{chr(10).join(lines)}
"""


def build_letter_user(posting_text: str, company: str, role_title: str,
                      accepted: list, studies: list[CaseStudy]) -> str:
    bullets = "\n".join(f"- {a.text}" for a in accepted)
    cases = "\n".join(f"- {c.title}: {c.text} ({c.url})" for c in studies) \
        or "- none available for this lens"
    return f"""\
Write the body of a cover letter for {role_title} at {company}.

EXACTLY ONE paragraph, and nothing else. No salutation, no closing line, no
sign-off, no second paragraph - those are added when the document is rendered
and a closing line from you becomes a duplicate.

**Five sentences at most. Shorter is better.**

Say why they fit. That is the paragraph's whole job.

**Do not restate what the list below already says.** The case studies are
rendered verbatim as links directly beneath your paragraph, each with its own
description and its own tech stack. So do not name the frameworks, languages or
services again, and do not re-describe a project the list already describes -
a reader gets it twice and the paragraph wastes its words. Refer to the SHAPE
of the work and why it maps to this role; let the list carry the specifics.

Concrete, not effusive. Name the company and the role - those are given below
and are not inventions. Every other specific must come from the material
here.

# The posting
{posting_text}

# The bullets already selected for their resume
{bullets}

# Case studies you may cite - cite the TITLE exactly, or cite none
{cases}
"""
