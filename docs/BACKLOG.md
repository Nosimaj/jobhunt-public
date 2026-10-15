# jobhunt — backlog

Planned and unstarted work. Status claims here are claims; verify before trusting one.

---

## PLANNED — Onboarding / setup page

**Status:** brainstorm in progress (2026-10-14). Two decisions made, no spec, no code.

**Goal.** Replace the README's "Make it yours" file-editing steps with a page in the
dashboard, so someone who doesn't edit YAML can go from "dashboard running" to
"jobs scored" without opening a file. Install still happens in the terminal
(clone, `pip install`, start the dashboard) — a one-click installer is out of scope.

**Decided:**

1. **Setup AND run (option B).** The page configures everything and also has
   *Fetch jobs* and *Score roles* buttons, so the terminal isn't needed after install.
2. **Every action that spends money shows a cost preview and needs a confirmation
   step** before it runs — profile build (~$0.05–0.15) and especially scoring, which
   is where an untiered run can cost $5–50 by accident (see the README's cost
   warning). The preview must show the role count and the estimate, and scoring must
   always be tiered by a title filter unless the user explicitly widens it.

**In scope (from the README's Make it yours list):**

- Claude API key → `.env` (written with mode 600, never echoed back, only a
  "key saved / key works" status shown).
- Job boards → add/remove entries in `seed_boards.yaml`, with per-provider hints for
  finding the token (Greenhouse / Ashby / Lever / Workable / Teamtailor / Workday
  `{tenant}.wdN/{site}`), and a "test this board" check.
- Résumé upload → `inputs/resume.{pdf,docx,txt,md}`; LinkedIn PDF →
  `inputs/linkedin.pdf`, with the tip: on your LinkedIn profile, **More → Save to PDF**.
- Case studies → `case_studies.yaml`, entered as text or uploaded as a file.
- Persona (`persona.yaml`: design summary, builder summary, positioning) and search
  preferences (`search.yaml`: seniority, domains, disqualifiers, home metros/states).
- *Build my profile* (runs ingest) → then point the user at `data/review.md` to check.
- A checklist showing which steps are done.

**Out of scope:** `overrides.yaml` and editing `titles.py` stay README-only (advanced).

**Constraints the spec must carry:**

- The dashboard is **read-only today** and binds `127.0.0.1` with no auth. This adds
  its first write paths, including a secret. Needs: localhost-only binding kept,
  protection against cross-site requests from other sites in the user's browser
  (CSRF / Origin check), upload size and type limits, no path traversal in file names,
  and never returning the API key in any response.
- Spending is POST-only (existing dashboard rule), and long runs (fetch/score) must not
  block the page — run in the background with visible progress.
- Editing `persona.yaml` or `search.yaml` changes the prompt fingerprint and makes
  every stored verdict stale; the page must warn before saving and the scoring
  preview must reflect the re-score.

**Rough size:** ~$25–45 API-equivalent to design and build natively, plus ~40% for the
run buttons with cost preview/confirmation.

**Next step when resumed:** continue the brainstorm — propose 2–3 approaches for the
page structure (single long page vs. step-by-step wizard vs. checklist hub), then the
sectioned design, then a written spec.
