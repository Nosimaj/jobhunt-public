# jobhunt — contributor orientation

Read `README.md` first for what the tool does and how a user personalises it. This
file is for people (and agents) changing the code: the conventions and the gotchas
that are verified and must not be re-derived.

```bash
.venv/bin/pytest                               # the whole suite, no API or network calls
.venv/bin/python -m jobhunt.matching --print-prompt   # read the scoring prompt, free
```

Always use `.venv/bin/pytest` and `.venv/bin/python`, never the bare commands.

## Layout

| Package | Responsibility |
|---|---|
| `jobhunt/` (top level) | ingest: `parse_linkedin_pdf`, `parse_resume`, `persona`, `overrides`, `snapshot`, `reconcile`, `review` |
| `jobhunt/discovery/` | HN polling, ATS parsing, `store.py` (SQLite, migrations) |
| `jobhunt/ats/` | polling the job boards |
| `jobhunt/matching/` | prefilter (`titles.py`), geography, the two rubrics, the scorer, the CLI |
| `jobhunt/generation/`, `generate/` | drafting and rendering applications, anti-fabrication guards |
| `jobhunt/dashboard/` | read-only FastAPI + htmx page; `DESIGN.md` governs its look |

## Conventions

- **TDD.** Failing test first, then the code.
- **Clients and fetchers are injected.** No test makes a live API or network call.
- **Pure functions are separated from I/O**, so fixtures are text, not binaries.
- **SQLite via the standard library, no ORM.** `PRAGMA user_version` plus an
  append-only migration list. **Never edit a migration that has already been
  applied. Add a new version.** A database stamped at version N never replays an
  edited version N.
- **Failures are loud.** A missing source, an unresolvable employer or an empty
  résumé raises with the path or the fix. Partial success is never reported as
  success.
- Commit prefixes: `feat:`, `fix:`, `test:`, `docs:`.

## Rules that must not be broken

1. **Precedence.** Dates, employers and chronology come from the LinkedIn PDF.
   Prose, lens tags, skills and projects come from the user's résumé. Titles are
   kept twice (`title_of_record`, `title_presented`) and never reconciled.
2. **The audience firewall.** PDF bullets are `untagged`: imported, never reviewed.
   **No PDF bullet may ever be `both`** (a validator enforces it), and generation
   never places an `untagged` bullet in a document.
3. **`render: feature | minimal | omit` is the one prominence knob.** An omitted role
   stays in `profile.json`. Omission is a rendering decision, not a deletion, so the
   chronology stays intact. Don't invent a second mechanism.
4. **Never read the LinkedIn PDF's free-text fields.** The exporter destroys newlines
   and double-escapes entities. Summaries come from `persona.yaml`.
5. **The persona never comes from a model.** `parse_resume` extracts structure;
   positioning and the lens summaries are written by the user.

## Gotchas — verified, do not re-derive

- **Staleness is keyed on the rendered prompt** (`role_prompt_sha`: profile, rubric,
  `search.yaml` and model). Any change to those re-queues every scored role. That is
  correct behaviour and also the project's most expensive foot-gun. See the README.
- **`re.I` on an abbreviation is a bug.** `\bUS\b` under `re.I` matched the pronoun
  "us" in 39 of 260 posts. Country, state and acronym patterns (`US`, `ME`, `GTM`) are
  matched **case-sensitively**, scoped with `(?-i:...)` where the dict is `re.I`.
- **Every pattern is word-boundary anchored.** `ux` matches "Linux"; `revenue` once
  dropped "Senior Product Designer, Consumer Revenue". In `titles.py` a denylist hit
  is **overruled by any positive signal** in the same title, and the default is KEEP.
  The prefilter exists for cost control, not quality.
- **Audits read `postings_eligible_for_prefilter`, never `postings_needing_role_score`.**
  The latter excludes already-dropped rows, so sourcing an audit from it makes
  loosening a filter a silent no-op. A module-level test that proves a fact says
  nothing about whether the caller ever reaches it. Test the seam.
- **A valid-but-wrong `--title-match` regex is the real hazard.** It matches nothing
  and exits 0. The run always prints `N of M matched` and warns on an empty match.
- **An empty or failed board closes nothing.** Closure is inferred only from a
  successful, non-empty response.
- **Geography is never rendered into a rubric.** If the role rubric knew where the
  user lives, it would reject good roles on relocation grounds. Reach is a separate,
  deterministic filter (`matching/geography.py`).
- **Adaptive thinking tokens count against `max_tokens`.** Truncation arrives either
  as a `json_invalid` `ValidationError` or as `stop_reason == "max_tokens"`, and
  `Scorer._call` diagnoses both. Route new structured calls through it.
- **`messages.parse` validates inside the call**, so schema violations raise
  `pydantic.ValidationError` past the `anthropic.*` handlers. They must be wrapped.
- **Field `description=` instructs the model; `ge`/`le`/`min_length` only reject.**
  Restate constraints in prose and keep floors low enough that real output passes.
- **Spending is POST-only on the dashboard**, and one in-process lock per role stops
  concurrent clicks from paying twice.
- **Durations are inclusive** (`(y2-y1)*12 + (m2-m1) + 1`), and employment spans are
  merged before gaps are measured.
- **LinkedIn PDF dates use U+00A0**, not spaces. Normalise before any regex, or the
  parser silently finds nothing.

## Test fixtures

All personal fixtures are anonymised around a fictional "Sam Example". The prose is
deterministically scrambled, so it reads as nonsense on purpose. `profile_golden.json`
stands in for `data/profile.json` on a fresh clone (see `tests/conftest.py`). Never
commit a real person's résumé, contact details or employment history.
