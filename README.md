# jobhunt

A local job discovery and application-preparation tool. It pulls open roles from
company job boards, scores each one against **your** résumé and positioning with
Claude, and drafts a tailored résumé and cover letter for the ones worth your time.
It never submits anything on your behalf.

Runs on your machine. No server, no accounts. You need Python 3.11+ and an
[Anthropic API key](https://console.anthropic.com/).

It was built by a designer who codes, so out of the box it is tuned for design, UX,
product and design-engineering roles. The [Make it yours](#make-it-yours) section
shows how to retune it for your own career.

---

## Quickstart

```bash
git clone <this repo> jobhunt && cd jobhunt
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,dashboard]"
.venv/bin/pytest                      # should be all green, no API calls
```

Then follow **Make it yours** below, and run:

```bash
.venv/bin/python -m jobhunt.ingest              # your résumé -> data/profile.json
.venv/bin/python -m jobhunt.ats --seed          # once: register the boards in seed_boards.yaml
.venv/bin/python -m jobhunt.ats                 # poll every board for open roles
.venv/bin/python -m jobhunt.matching --status   # what's waiting, and what it would cost
.venv/bin/python -m jobhunt.matching --two-axis --title-match 'design|ux|product'
.venv/bin/python -m jobhunt.dashboard           # browse the results at http://127.0.0.1:8765
```

---

## Make it yours

Work through these in order. Every file except `.env`, `persona.yaml` and `inputs/` is
an example you edit in place.

| # | File | What it controls |
|---|---|---|
| 1 | `.env` | Your API key. `cp .env.example .env`, paste the key, `chmod 600 .env`. |
| 2 | `inputs/` | Your two résumé files. See [Your résumé](#your-résumé). |
| 3 | `persona.yaml` | Who you are: a designer summary, a builder summary, and your **positioning** (what roles you win, and what you lose and to whom). `cp persona.example.yaml persona.yaml`. |
| 4 | `search.yaml` | What you want: seniority, the domains you care about, company disqualifiers, your home metros and states (for "can I commute there"). |
| 5 | `seed_boards.yaml` | Which companies to poll. Each entry is a Greenhouse, Ashby, Lever, Workable or Teamtailor board token. Find it in the company's careers-page URL. |
| 6 | `case_studies.yaml` | Your published work. The cover-letter writer may only cite what's in here. |
| 7 | `overrides.yaml` *(optional)* | Employer aliases, roles to add by hand, and how prominently each role appears: `render: feature \| minimal \| omit`. Gitignored. |
| 8 | `src/jobhunt/matching/titles.py` *(if you're not in design/product)* | The cheap title prefilter that decides which roles are worth scoring at all. Add your discipline's titles to `POSITIVE_RE`. |

After editing 3, 4 or 8, re-run `ingest` (for 3) and read the cost warning below
before re-scoring.

### Your résumé

`ingest` reads two files from `inputs/`:

1. **`inputs/linkedin.pdf`**: your LinkedIn profile export (on your profile, click
   **More → Save to PDF**). This is the source of truth for **dates and employers**.
2. **`inputs/resume.pdf`**, **`.docx`**, **`.txt`** or **`.md`**: your normal résumé.
   This is the source for **bullet prose, skills and projects**, because it's in your
   voice. Old `.doc`, `.pages` and `.odt` files aren't supported. Export them as PDF
   or DOCX.

You can keep them elsewhere and point at them with `JOBHUNT_LINKEDIN_PDF` and
`JOBHUNT_RESUME`, either in `.env` or exported in your shell.

Your résumé is structured by one Claude call per `ingest` (cents, not dollars). Each
bullet is tagged `design`, `eng` or `both`, so a generated résumé can lean toward the
role it's for. **Then open `data/review.md` and `data/profile.json` and check them.**
The model can misread a résumé, and the profile is what everything downstream trusts.
The two sources will disagree on titles, and that's expected: both are kept, as
`title_of_record` and `title_presented`.

### ⚠️ The expensive foot-gun

Every stored verdict is fingerprinted by the exact prompt that produced it. **Editing
`persona.yaml` (then re-ingesting), `search.yaml`, the rubric text, or the model
marks every scored role stale**, and the next untargeted run re-scores all of them.
At roughly $0.03 a role, a corpus of 2,000 roles is about $60.

So always run `matching --status` first and read the "awaiting a role verdict" count.
Then score in **tiers** with `--title-match`, most promising titles first:

```bash
.venv/bin/python -m jobhunt.matching --two-axis --title-match 'design|ux|research'
.venv/bin/python -m jobhunt.matching --two-axis --title-match 'product'
```

Every run prints `N of M candidate(s) matched`. A typo in the regex matches nothing,
exits 0, and looks exactly like a finished tier, so read that line.

---

## How it works

```
inputs/ ──► ingest ──► data/profile.json ─────────────┐
                                                      ▼
seed_boards.yaml ──► ats ──► data/jobs.sqlite ──► matching ──► dashboard
HN "Who is hiring?" ──► discovery ──┘   (company tokens)    │
                                                            ▼
                                                   generate ──► data/applications/
```

- **`ingest`** merges your LinkedIn export and your résumé into one profile, and
  writes `data/review.md`, a worklist of everything it couldn't resolve. Conflicts
  are reported, never silently resolved.
- **`ats`** polls public job-board APIs for every registered company. A board that
  fails or returns nothing never closes your stored roles.
- **`discovery`** reads Hacker News's monthly "Who is hiring?" thread, mostly to find
  *companies* (it harvests their job-board tokens), not roles.
- **`matching`** scores each role on two separate axes: **role fit** (could you win
  it, and would you want it?) and **company fit** (would you want a conversation there
  at all?). Each verdict carries a mandatory *concern*, the strongest argument
  against applying. Engineering-titled roles must earn their place, either through a
  real design duty quoted from the posting or a match to one of your `role_domains`.
- **`dashboard`** is a read-only local page of the results, bound to `127.0.0.1`.
- **`generate <posting_id>`** drafts `resume.md` and `cover-letter.md`. You edit
  them; `generate --render DIR` turns your edited files into `.txt`, `.html`
  (print to PDF from the browser) and `application.json`. Anti-fabrication guards
  refuse invented employers, numbers or case studies.

`matching --print-prompt` shows the full scoring prompt as text, with zero API calls.
Read it once. It's the clearest description of what the tool thinks "fit" means.

---

## Data and privacy

Everything personal stays local and gitignored:

| Path | Holds |
|---|---|
| `inputs/` | your résumé files |
| `persona.yaml` | your positioning |
| `overrides.yaml` | your positioning decisions about specific roles |
| `data/` | your generated profile, the jobs database, and drafted applications |
| `.env` | your API key |

`.gitignore` only protects files that were **never committed**. If one of these does
reach a commit, deleting it later isn't enough. The history has to be rewritten.

---

## Contributing

Issues and pull requests are welcome. Read `CLAUDE.md` first: it lists the
conventions and the hard-won gotchas (several of them cost real money to find). In
short:

- TDD. Write a failing test, confirm it fails, then implement.
- No test makes a live API or network call. Clients and fetchers are injected.
- Fixtures are anonymised. The persona in `tests/fixtures/` is fictional ("Sam
  Example"). Never commit a real person's résumé.
- `.venv/bin/pytest` must stay green.

Good first contributions: more job-board providers (Rippling, SmartRecruiters,
Workday); a non-design default `titles.py`; and a single-résumé mode that doesn't
need the LinkedIn export.

## License

MIT. See `LICENSE`.
