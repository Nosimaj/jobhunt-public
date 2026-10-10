"""The two fit rubrics, rendered as text.

Pure: every function here is a string builder, so the prompts are readable and
testable with no API key and no spend.

WHY TWO. A single rubric was asked to hold both "is this the right job?" and
"is this the right company?", and on 2026-09-28 the two leaked into each other
in both directions. an engineering role at an infrastructure company
- scored `strong` because its stack and domain resembled something they had
built. Three companies they wanted a conversation with scored `no` because their
posted roles were wrong. One axis had nowhere to put "right company, wrong
opening", which is the single most common shape in the data.

The rubrics therefore know nothing about each other, and neither knows the
action its verdict produces - see `fit.ACTION`, which lives in code.
"""

from __future__ import annotations

import hashlib

from .search_config import SearchConfig

# --------------------------------------------------------------------------
# Rubric A - role fit, one call per posting.
# --------------------------------------------------------------------------

_ROLE_RUBRIC = """\
# Your task

You are scoring whether a specific job posting is a role the candidate can
credibly win and would want. Score the ROLE, not the company. A great company
with the wrong opening is a `no` here - company appeal is scored separately and
must not influence this verdict.

Target seniority: {seniority_range}.

# The core steering rule

{positioning}

Therefore:

- Design, UX and product-design roles: evaluate normally.
- Engineering roles: `no` UNLESS they pass the Hybrid Test below OR match one
  of their domains (see Domain relevance and the floor below).
- Product-management roles: `no` unless the posting explicitly wants a
  designer-PM or builder-PM - prototyping, design ownership, or hands-on
  technical work stated as a responsibility.

# The Hybrid Test (engineering roles only)

A role is hybrid ONLY if the posting assigns at least one of these as an actual
responsibility, in the duties or requirements and not in the company blurb:

- user research, usability testing, or talking directly to end users to shape
  the product
- UX, interaction or visual design, or owning the product experience end to end
- prototyping for the purpose of exploring or validating design direction
- authoring tools, design systems, or interfaces for designers and
  non-engineers

Titles are NOT evidence. "Product Engineer", "Design Engineer", "Founding
Engineer" and "Full-stack" pass only if the DUTIES pass. Stack or domain
overlap - Python, FastAPI, agents, healthcare - is NOT evidence of a hybrid
duty (domain relevance is judged separately below). It makes a qualified
engineering role more attractive; it cannot convert an engineering role into a
hybrid one.

For an engineering role you must choose `pass`, `weak` or `fail`; `n/a` is
only for non-engineering roles.

If you find yourself arguing that an engineering role is hybrid because it
resembles something The candidate built, stop: resemblance is not a hybrid pass.
Record it under `domain_fit` instead. Apply the Hybrid Test literally, and
quote the duty phrases you relied on.

# Domain relevance

These are the kinds of work their record actually supports:

{role_domains}

Set `domain_fit`:
  - `direct`   - the posting's OWN duties sit in one of those domains.
  - `adjacent` - they neighbour one without landing in it.
  - `none`     - neither.

Judge the ROLE, not the company. An internal-tools or design-systems role at
a company that sells something else entirely is still `direct`; a role with
nothing to do with those domains at a company in one of them is `none`.
`direct` and `adjacent` both require `domain_evidence` - duty phrases copied
from the posting that put the role in, or next to, one of those domains. An
industry is not evidence, and a company's name is not evidence.

# The floor, for ENGINEERING-titled roles only

An engineering role must earn its place in one of two ways: a hybrid pass
(the posting assigns a qualifying design duty), OR a domain match (`direct`
or `adjacent`). With NEITHER, `role_fit` is `no` - they lose those roles to
career engineers, and resemblance is a reason they would enjoy a role rather
than a reason they would be hired.

A `weak` hybrid still counts as earning a place, but it caps `role_fit` at
`partial`, whatever the domain says. An `adjacent`-only match (no hybrid pass,
domain not `direct`) also caps `role_fit` at `partial`. Only a hybrid pass or a
`direct` domain match may be `strong`.

This floor does NOT apply to `design` or `design_engineering` roles. Judge
those on discipline, seniority and overlaps exactly as before - a design role
outside these domains is still a design role they can do.

# Verdicts

- `strong`: primary discipline is design, UX or design-engineering, OR it
  passes the Hybrid Test clearly, OR (engineering) it is a `direct` domain
  match; seniority in range; at least two concrete requirement overlaps with
  their record.
- `partial`: right discipline but one real gap - a seniority stretch, one
  missing domain requirement, or heavy weight on a sub-skill they are light on -
  OR it passes the Hybrid Test only weakly, OR (engineering) it is an
  `adjacent`-only domain match.
- `no`: wrong discipline, an engineering role with neither a hybrid pass nor a
  domain match, seniority far out of range, or a hard requirement they cannot meet (a licence, a degree stated as mandatory
  with no equivalence, a clearance, a relocation they are excluded from).

Be decisive and be willing to say no. Most postings are a `no`.
"""

# --------------------------------------------------------------------------
# Rubric B - company fit, one call per company, cached.
# --------------------------------------------------------------------------

_COMPANY_RUBRIC = """\
# Your task

You are scoring whether a COMPANY is one the candidate would want a
conversation with, independent of any specific opening. Ignore what roles are
currently posted. The question is: if a door opened here, would they want to walk
through it?

You will often be working from very little - frequently just the text of a job
advertisement. Say `unknown` when the text does not tell you. Silence is not
evidence of absence, and a wall of `unknown` is a correct answer to a thin
source. Only mark a signal `absent` when the text positively shows it missing.

# What pulls them toward a company

1. Domain pull - does it work in one of these?
{target_domains}

2. Designs how things work - does it create or improve something people use: a
   product, a service, a machine, a system, or a process? Service design counts
   fully, including redesigning an existing automated service or machine to add
   or fix an attribute - an ice cream maker that runs too cold in winter, and
   someone applying design thinking to fix it, counts. New ways of getting
   something done count as much as new products. Mark this `absent` only when
   the core business is ad-tech, performance marketing, or staffing and
   recruiting - the resale of attention or labour rather than improving how
   something works.

3. Design is valued - evidence that it hires designers, talks about craft or
   UX, or has design-led products. The absence of any design function is a
   NEGATIVE signal, not a neutral one.

4. Stage and scale where a senior generalist matters: {stage_preference}.

5. Reachability - Boston or remote-friendly, or a founding and leadership team
   plausibly reachable for outreach.

# Disqualifiers - any one of these forces `no`

{company_disqualifiers}

# Verdicts

- `target`: domain pull present AND at least two of signals 2-5 present, with
  no disqualifier.
- `neutral`: nothing disqualifying, but domain pull absent or mostly unknown.
- `no`: any disqualifier, or signals 2 and 3 both clearly absent.

Growth, marketing and ops-heavy companies can still be `target` if the growth is
tied to product or R&D. Judge what the company builds, not which department
happens to be hiring.
"""


def _render_roles(profile: dict) -> str:
    out = []
    for r in profile["roles"]:
        if r.get("render") == "omit":
            continue                      # not offered as evidence at all
        head = (f"### {r['company']} - {r['title_presented']} "
                f"({r['start']} to {r['end']})")
        lines = [head]
        for b in r["bullets"]:
            mark = "" if b["audience"] != "untagged" else "  [unreviewed]"
            lines.append(f"- {b['text']}{mark}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def build_role_system(profile: dict, cfg: SearchConfig) -> str:
    """The cached system block for rubric A. Identical for every posting."""
    skills = "\n".join(f"- [{s['audience']}] {s['text']}"
                       for s in profile["skills"])
    projects = "\n".join(f"- {p['title']} - {p['detail']}"
                         for p in profile.get("projects", []))
    return f"""\
You are screening job postings for one specific candidate.

# Who the candidate is

## As a designer
{profile['summaries']['design']}

## As a builder
{profile['summaries']['eng']}

## Skills
{skills}

## Published work
{projects or '- none recorded'}

## Experience
{_render_roles(profile)}

Bullets marked `[unreviewed]` are imported verbatim from their LinkedIn export and
they have not edited them. Treat them as evidence of what they have done; do not treat
their wording as their own.

{_ROLE_RUBRIC.format(
    positioning=profile.get('positioning') or '',
    seniority_range=cfg.seniority_range,
    role_domains=cfg.bullets("role_domains"))}
"""


def build_company_system(cfg: SearchConfig) -> str:
    """The cached system block for rubric B.

    Carries no profile: company fit does not depend on their resume, which is
    what lets a cached company verdict survive a re-run of ingestion.
    """
    return _COMPANY_RUBRIC.format(
        target_domains=cfg.bullets("target_domains"),
        stage_preference=cfg.stage_preference,
        company_disqualifiers=cfg.bullets("company_disqualifiers"))


def build_role_user(posting) -> str:
    """The posting text and nothing else, so the judgement is absolute rather
    than comparative against whatever else shared a batch."""
    return f"Judge this job posting:\n\n{posting.raw_text}"


def build_company_user(company: str, source_text: str) -> str:
    """Everything known about one company.

    `source_text` is injected rather than read here, so the source can grow
    from "the company's postings" to a fetched careers page without touching
    this module - see the deferred ATS work in the discovery spec.
    """
    return (f"Judge this company: {company}\n\n"
            f"Everything known about them:\n\n{source_text}")


def _sha(blob: str) -> str:
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def role_prompt_sha(profile: dict, cfg: SearchConfig, model: str) -> str:
    """Fingerprint of everything that decides a role verdict: the rendered
    system block - so the profile AND the rubric AND the config - plus the
    model. A stored verdict whose fingerprint differs is stale."""
    return _sha(build_role_system(profile, cfg) + "\n" + model)


def company_prompt_sha(cfg: SearchConfig, model: str) -> str:
    """Deliberately excludes the profile. A cached company verdict must not be
    invalidated by re-running ingestion - nothing about their resume changes what
    a company builds."""
    return _sha(build_company_system(cfg) + "\n" + model)
