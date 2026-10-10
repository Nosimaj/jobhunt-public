from __future__ import annotations

import hashlib

from .rubrics import _render_roles

RUBRIC = """\
## How to judge

One question decides most of this: **does this role explicitly want someone who spans disciplines?**

{positioning}

### The hybrid gate

A posting can only be `strong` if the hybrid need is signalled, by EITHER:

- **explicit**: a hybrid title (Design Engineer, Product Engineer, Design Technologist,
  UX Engineer, Technical PM, Founding Designer, Prototyper), or body language such as
  "designer who codes", "own design and implementation", "prototype in code";
- **contextual**: founding or very small team, "wear many hats", generalist framing.
  Small companies write bad job posts - a four-person company posting "Software Engineer"
  may genuinely want a designer who codes.

If neither is present, set `hybrid_signal` to `none` and the bucket CANNOT be `strong`.
Cap it at `maybe` or `no`.

**A signal is not permission.** The gate only says what a role CANNOT be; it never
promotes one. The near-automatic `no` list below OVERRIDES a `contextual` signal
outright: a small company posting an engineering-titled role is still a `no`. Only an
`explicit` signal can override that list. Nearly every company on this board is a
startup, so "small team" on its own decides nothing.

Two titles that look hybrid and are not: **"Founding Engineer"** is an engineering role,
and a posting that says it wants **"product-minded"** or **"product-focused" engineers**
is asking for an engineer with taste, not for a designer. Neither is an `explicit`
signal. If a posting never mentions design, it is not a design role however small the
company.

### Near-automatic `no`

- Pure engineering titles: Software / Platform / Backend / Frontend / Security / SRE /
  Data / Founding Engineer. **Even when the technology stack matches their exactly.**
  Set `discipline` to `engineering` for these; `discipline: engineering` with
  `hybrid_signal` other than `explicit` is a `no`, whatever the title is paraphrased as.
- **"full-stack" anywhere in the role title.**
- Brand, marketing or visual-identity design - unless the positioning above says
  that is the candidate's lane.
- Generic product management with no technical or design signal.
- Growth or marketing with no R&D framing. Growth tied to design sprints, research and
  exploration is wanted; performance marketing is not.

### Buckets are actions, not enthusiasm

- `strong` - "I would spend an hour writing a tailored application for this."
- `maybe` - "worth two minutes' reading before deciding."
- `no` - "I would not apply."

### Calibration

**The overwhelming majority of postings are `no`, and that is the correct outcome.**
`strong` is rare - a handful out of a hundred. If you are returning it often, you are
wrong. `maybe` is not a way to avoid deciding: use it only for the genuinely split
cases described above.

**Word overlap is not fit.** Worked example (invented, not from this board): a
300-person payments company posting "Software Engineers (Senior, Staff, Principal)"
shares the candidate's seniority and lists their exact languages, and is a `no` - they want a
distributed-systems engineer and they have shipped a FastAPI application.

**Score the ROLE, not the posting.** Many postings list several roles. Choose the single
best-fitting role, put its exact title in `role_title`, and judge THAT role. A posting
offering both "Senior Design Engineer" and "Senior Platform Engineer" is judged on the
design role.

**Never model the candidate's confidence.** Do not reason about whether they would feel qualified
or could keep up. Judge role fit only. They are systematically conservative about their own
capability and reproducing that bias would reject roles they would be strong in.

**`fit_score` must agree with the bucket, not argue with it.** `no` is 0-39, `maybe`
is 40-69, `strong` is 70-100. An unanchored score becomes a way to smuggle enthusiasm
past the bucket definitions.

**Name a concern even when you say `strong`.** `concern` is the single strongest argument
against applying. Every role has one, and "none" is not a concern - if you cannot
name one, you have not read the posting closely enough.
"""


def build_system(profile: dict) -> str:
    """The cached system block. Identical for every posting in a run."""
    skills = "\n".join(f"- [{s['audience']}] {s['text']}" for s in profile["skills"])
    projects = "\n".join(f"- {p['title']} - {p['detail']}"
                         for p in profile.get("projects", []))
    return f"""\
You are screening job postings for one specific candidate. For each posting
you return a structured verdict. Be decisive and be willing to say no.

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

Bullets marked `[unreviewed]` are imported verbatim from their LinkedIn export and they have
not edited them. Treat them as evidence of what they have done; do not treat their wording
as their own.

{RUBRIC.replace('{positioning}', profile.get('positioning') or '')}
"""


def build_user(posting) -> str:
    """The per-posting block. The posting text and nothing else, so the
    judgement is absolute rather than comparative."""
    return f"Judge this job posting:\n\n{posting.raw_text}"


def prompt_sha(profile: dict, model: str) -> str:
    """Fingerprint of everything that decides a verdict: the fully rendered
    system prompt (so the profile AND the rubric) plus the model id. A stored
    verdict whose fingerprint differs is stale and must be re-scored.

    Keyed on the profile alone - as this was originally - it would miss the
    single most likely change between two runs: an edit to the rubric, which
    is the entire reason for running `--eval`. The eval would then reuse the
    stored verdicts and confidently report a score for a prompt that no
    longer exists. A `--model` swap had the same hole.

    Deriving it from `build_system` rather than from the profile dict also
    gets the old behaviour for free: `generated_at` and `sources` are not
    rendered into the prompt, so re-running ingest with no substantive change
    does not invalidate anything.
    """
    blob = build_system(profile) + "\n" + model
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
