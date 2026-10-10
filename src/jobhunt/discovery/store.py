from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import DiscoveryRun, Posting

STALE_DAYS = 45

#: How long a blocked statement waits for a lock before raising. The dashboard
#: reads while the matching CLI writes; without this, a reader raises
#: `database is locked` the instant it meets a write lock.
BUSY_TIMEOUT_MS = 5000


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

#: The schema version a database is stamped with once `init_schema` has
#: brought it fully up to date. Bump it and append to `_MIGRATIONS` together.
SCHEMA_VERSION = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
  id                  TEXT PRIMARY KEY,
  source              TEXT NOT NULL,
  source_thread_id    TEXT NOT NULL,
  company             TEXT NOT NULL,
  company_normalised  TEXT NOT NULL,
  -- Cleaned plain text, with ONE documented exception: a posting whose
  -- status is 'rejected'/'parse_error' AND whose text cleaned to nothing
  -- holds the raw HN markup, prefixed '<unparsed raw markup> '. Every
  -- other row, parse failures included, holds cleaned plain text.
  raw_text            TEXT NOT NULL,
  apply_url           TEXT,
  is_remote           INTEGER NOT NULL,
  geo_signal          TEXT NOT NULL,
  posted_at           TEXT NOT NULL,
  first_seen          TEXT NOT NULL,
  last_seen           TEXT NOT NULL,
  status              TEXT NOT NULL,
  reject_reason       TEXT,
  role_title          TEXT,
  discipline          TEXT,
  hybrid_signal       TEXT,
  match_reason        TEXT,
  match_concern       TEXT,
  fit_score           INTEGER,
  match_bucket        TEXT,
  location            TEXT,
  salary              TEXT,
  matched_at          TEXT,
  match_model         TEXT,
  match_profile_sha   TEXT,
  -- Two-axis fit (v4). `action` is DERIVED from role_fit + company_fit and
  -- stored so it can be queried; nullable because a posting with only one
  -- axis scored has no action, and a placeholder would read as a decision.
  role_fit            TEXT,
  role_discipline     TEXT,
  hybrid_test         TEXT,
  hybrid_evidence     TEXT,
  role_overlaps       TEXT,
  role_gaps           TEXT,
  role_reason         TEXT,
  seniority_note      TEXT,
  action              TEXT,
  role_scored_at      TEXT,
  role_model          TEXT,
  role_prompt_sha     TEXT,
  -- v5: ATS role-source columns, populated once a posting is pulled from a
  -- company's own board rather than an HN thread.
  workplace_type      TEXT,
  department          TEXT,
  closed_at           TEXT,
  -- v6: the prefilter's decision. NULL means "not dropped".
  prefilter_drop      TEXT,
  -- v7: reachability (remote/local/out_of_area/unknown). NULL = not yet audited.
  reach               TEXT,
  -- v8: domain relevance for engineering roles (direct/adjacent/none).
  domain_fit          TEXT,
  domain_evidence     TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  -- Which pipeline wrote this row: 'discovery' (slice 2) or 'match' (slice 3).
  -- Load-bearing. Discovery's heartbeat asks "when did a poll last succeed?",
  -- and without this column a matching run answers that question - silencing
  -- both the "last successful run was N days ago" and the "most recent run
  -- FAILED" warnings, which is a total-outage-looks-healthy failure.
  kind          TEXT NOT NULL DEFAULT 'discovery',
  thread_id     TEXT,
  posts_seen    INTEGER NOT NULL DEFAULT 0,
  candidates    INTEGER NOT NULL DEFAULT 0,
  rejected      INTEGER NOT NULL DEFAULT 0,
  new_postings  INTEGER NOT NULL DEFAULT 0,
  error         TEXT
);
CREATE TABLE IF NOT EXISTS companies (
  company_normalised      TEXT PRIMARY KEY,
  company                 TEXT NOT NULL,
  company_fit             TEXT NOT NULL,
  domain_pull             TEXT NOT NULL,
  designs_how_things_work TEXT NOT NULL,
  design_valued           TEXT NOT NULL,
  stage_fit               TEXT NOT NULL,
  reachability            TEXT NOT NULL,
  disqualifier            TEXT,
  reason                  TEXT NOT NULL,
  scored_at               TEXT NOT NULL,
  model                   TEXT NOT NULL,
  prompt_sha              TEXT NOT NULL,
  -- v5: the ATS board registry.
  ats_provider            TEXT,
  ats_token               TEXT,
  ats_last_ok             TEXT,
  ats_last_error          TEXT
);
"""

#: Ordered `(version, sql)` pairs. `init_schema` applies every entry whose
#: version exceeds the database's `PRAGMA user_version`, in order, then stamps
#: it. Already-applied entries are skipped, so the list is append-only and
#: never edited in place.
#:
#: To add a column in a later slice, do BOTH: add it to `_SCHEMA` (so a fresh
#: database is created with it) and append an `ALTER TABLE` migration (so an
#: existing database gains it). Doing only the first is a no-op against the
#: one database that holds real data, and every read then fails with
#: `no such column` once `_COLUMNS` grows.

_MIGRATION_V2 = """
ALTER TABLE postings ADD COLUMN role_title        TEXT;
ALTER TABLE postings ADD COLUMN discipline        TEXT;
ALTER TABLE postings ADD COLUMN hybrid_signal     TEXT;
ALTER TABLE postings ADD COLUMN match_reason      TEXT;
ALTER TABLE postings ADD COLUMN match_concern     TEXT;
ALTER TABLE postings ADD COLUMN fit_score         INTEGER;
ALTER TABLE postings ADD COLUMN match_bucket      TEXT;
ALTER TABLE postings ADD COLUMN location          TEXT;
ALTER TABLE postings ADD COLUMN salary            TEXT;
ALTER TABLE postings ADD COLUMN matched_at        TEXT;
ALTER TABLE postings ADD COLUMN match_model       TEXT;
ALTER TABLE postings ADD COLUMN match_profile_sha TEXT;
ALTER TABLE runs     ADD COLUMN kind TEXT NOT NULL DEFAULT 'discovery';
"""

#: v3 exists ONLY to repair databases stamped v2 that never received
#: `runs.kind`.
#:
#: `ALTER TABLE runs ADD COLUMN kind` was appended to `_MIGRATION_V2` in
#: b70edf6, an hour AFTER e6f04d4 had already migrated the real database to
#: v2. `_apply_migrations` skips any version <= the stamped one, so that
#: database never replayed v2 and never got the column - and `last_run`,
#: `last_successful_run`, `failed_runs_since` and `start_run` all reference
#: it, so every matching command including `--status` died on
#: `no such column: kind`.
#:
#: **Editing an applied migration cannot work.** The version stamp is the only
#: record of what ran; changing the script behind it changes history nobody
#: replays. Add a version instead - that is what this is.
#:
#: Re-issuing the same ALTER is safe: `_apply_migration_sql` skips an
#: ADD COLUMN whose column already exists, which is the common case here since
#: most databases received `kind` from the edited v2.
_MIGRATION_V3 = """
ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'discovery';
"""

#: v4 adds the two-axis fit columns (slice 3 amendment).
#:
#: Role fit lives on `postings` because it is per posting. Company fit lives in
#: its own table because it is per COMPANY.
#:
#: MEASURED 2026-09-28, against the September thread: 131 distinct companies
#: across 132 candidates - 130 with one posting, one with two. So WITHIN a
#: single HN thread the cache saves exactly one call, not the large fraction
#: an earlier version of this comment claimed. "Who is hiring?" is one post
#: per employer by convention.
#:
#: The cache still earns its place, for two reasons that are not about a
#: single run: HN regulars repost month after month, so a company scored in
#: September is free in October (unmeasured - only one thread is stored so
#: far); and one company judgement shared by every posting cannot drift
#: between them. But it does mean the second axis roughly DOUBLES the cost of
#: a first run rather than adding a little.
#:
#: `action` is DERIVED from the pair and stored so it can be queried and
#: indexed. It is deliberately nullable: a posting with only one of the two
#: axes has no action, and a zero or a placeholder there would read as a
#: decision that has not been made.
_MIGRATION_V4 = """
ALTER TABLE postings ADD COLUMN role_fit         TEXT;
ALTER TABLE postings ADD COLUMN role_discipline  TEXT;
ALTER TABLE postings ADD COLUMN hybrid_test      TEXT;
ALTER TABLE postings ADD COLUMN hybrid_evidence  TEXT;
ALTER TABLE postings ADD COLUMN role_overlaps    TEXT;
ALTER TABLE postings ADD COLUMN role_gaps        TEXT;
ALTER TABLE postings ADD COLUMN role_reason      TEXT;
ALTER TABLE postings ADD COLUMN seniority_note   TEXT;
ALTER TABLE postings ADD COLUMN action           TEXT;
ALTER TABLE postings ADD COLUMN role_scored_at   TEXT;
ALTER TABLE postings ADD COLUMN role_model       TEXT;
ALTER TABLE postings ADD COLUMN role_prompt_sha  TEXT;
CREATE TABLE IF NOT EXISTS companies (
  company_normalised      TEXT PRIMARY KEY,
  company                 TEXT NOT NULL,
  company_fit             TEXT NOT NULL,
  domain_pull             TEXT NOT NULL,
  designs_how_things_work TEXT NOT NULL,
  design_valued           TEXT NOT NULL,
  stage_fit               TEXT NOT NULL,
  reachability            TEXT NOT NULL,
  disqualifier            TEXT,
  reason                  TEXT NOT NULL,
  scored_at               TEXT NOT NULL,
  model                   TEXT NOT NULL,
  prompt_sha              TEXT NOT NULL
);
"""

#: v5 adds the ATS board registry to `companies`, and the role columns the
#: ATS source needs on `postings`. Appended as a NEW version rather than
#: edited into v4 - editing an applied migration is what broke the real
#: database and required v3.
_MIGRATION_V5 = """
ALTER TABLE companies ADD COLUMN ats_provider   TEXT;
ALTER TABLE companies ADD COLUMN ats_token      TEXT;
ALTER TABLE companies ADD COLUMN ats_last_ok    TEXT;
ALTER TABLE companies ADD COLUMN ats_last_error TEXT;
ALTER TABLE postings  ADD COLUMN workplace_type TEXT;
ALTER TABLE postings  ADD COLUMN department     TEXT;
ALTER TABLE postings  ADD COLUMN closed_at      TEXT;
"""

#: v6 adds the prefilter's decision. NULL means "not dropped". Stored rather
#: than computed-and-forgotten so a drop is auditable, and recomputed on every
#: run so loosening the filter un-drops automatically.
_MIGRATION_V6 = """
ALTER TABLE postings ADD COLUMN prefilter_drop TEXT;
"""

#: v7 adds the reachability decision. NULL means "not yet audited" and stays
#: scorable. Never append to this once applied - add v8 instead.
_MIGRATION_V7 = """
ALTER TABLE postings ADD COLUMN reach TEXT;
"""

#: v8 adds domain relevance. A separate version from v7 on purpose: v7 was
#: already applied and stamped, and an applied migration never replays.
_MIGRATION_V8 = """
ALTER TABLE postings ADD COLUMN domain_fit TEXT;
ALTER TABLE postings ADD COLUMN domain_evidence TEXT;
"""

#: Indexes are created AFTER migrations, never inside `_SCHEMA`.
#:
#: `CREATE TABLE IF NOT EXISTS` is a no-op against an existing table, but the
#: `CREATE INDEX` beside it is NOT - it runs regardless, and an index on a
#: column that a pending migration has not added yet fails with
#: `no such column`. That took down `init_schema` for every pre-v4 database
#: the moment the v4 index was added, which is every command, because both
#: CLIs call it first. Keeping indexes here means they are built once the
#: columns are guaranteed to exist, on both the fresh and the migrated path.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_postings_company ON postings(company_normalised);
CREATE INDEX IF NOT EXISTS idx_postings_status  ON postings(status, posted_at);
CREATE INDEX IF NOT EXISTS idx_postings_bucket  ON postings(match_bucket, fit_score DESC);
CREATE INDEX IF NOT EXISTS idx_postings_action  ON postings(action, role_fit);
CREATE INDEX IF NOT EXISTS idx_companies_board ON companies(ats_provider, ats_token);
CREATE INDEX IF NOT EXISTS idx_postings_open   ON postings(closed_at, source);
CREATE INDEX IF NOT EXISTS idx_postings_prefilter ON postings(prefilter_drop);
"""

_MIGRATIONS: list[tuple[int, str]] = [
    # v1 is the schema as shipped in slice 2. `_SCHEMA` above already creates
    # exactly this shape, and a database created by an early slice-2 build has
    # it too while still being stamped 0 - so there is nothing to alter here,
    # only a version to stamp. It exists so the sequence starts at a known
    # point rather than at "whatever happened to be on disk".
    (1, ""),
    (2, _MIGRATION_V2),
    (3, _MIGRATION_V3),
    (4, _MIGRATION_V4),
    (5, _MIGRATION_V5),
    (6, _MIGRATION_V6),
    (7, _MIGRATION_V7),
    (8, _MIGRATION_V8),
]

#: The columns `Posting` carries, in order. `upsert_posting` INSERTs exactly
#: these and `_row_to_posting` reads exactly these, so the tuple and the model
#: must stay in step.
#:
#: `closed_at` (v5) is here because a role the board stopped listing is gone,
#: and a consumer that lists roles has to be able to say so. It is nullable
#: and defaulted on the model, so every existing `Posting(...)` construction
#: still satisfies `extra="forbid"`.
_COLUMNS = ("id", "source", "source_thread_id", "company", "company_normalised",
            "raw_text", "apply_url", "is_remote", "geo_signal", "posted_at",
            "first_seen", "last_seen", "status", "reject_reason", "closed_at")

#: What `upsert_posting` INSERTs, which is `_COLUMNS` minus `closed_at`.
#:
#: Closure is never known at insert time - it is written only by
#: `mark_closed` / `reconcile_board` and cleared by `upsert_ats_role` - so
#: naming it here would add nothing but a literal NULL. It would also break
#: the one path that writes BEFORE migrating: a v0 database has no
#: `closed_at`, and `test_a_database_stamped_at_zero_is_migrated_forward_
#: without_data_loss` inserts into exactly that.
_INSERT_COLUMNS = tuple(c for c in _COLUMNS if c != "closed_at")

# Every reader that answers "what is still worth acting on?" now carries
# `closed_at IS NULL`: `candidates`, `unscored`, `postings_needing_role_score`,
# `by_action` and `action_counts`. Before that `closed_at` had exactly one
# reader (`count_open_ats_roles`) and gated nothing, so a filled role was
# still re-scored and still recommended as an apply - and `action_counts`
# kept disagreeing with `by_action` even after the first round of fixes.


class Store:
    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        """Open the database.

        `read_only=True` opens via a URI in `mode=ro`, which both prevents
        writes and refuses to CREATE a missing file — so a typo'd path is an
        error rather than an empty page that looks like "no results".

        WAL is set once and persists in the file: readers no longer block the
        writer and the writer no longer blocks readers. It is NOT set on a
        read-only connection, because setting it is itself a write.
        """
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            uri = f"file:{self.path}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path)
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_MS)}")

    def journal_mode(self) -> str:
        return self._conn.execute("PRAGMA journal_mode").fetchone()[0].casefold()

    def busy_timeout(self) -> int:
        return int(self._conn.execute("PRAGMA busy_timeout").fetchone()[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def init_schema(self) -> None:
        """Create the schema if absent, then migrate it up to SCHEMA_VERSION.

        Idempotent. `CREATE TABLE IF NOT EXISTS` alone is not a migration
        path: it cannot add a column to a database that already exists, and
        `PRAGMA user_version` on a pre-migration database reads 0, so there
        was no way to tell how far behind it was.
        """
        fresh = self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master"
            " WHERE type = 'table' AND name = 'postings'").fetchone()[0] == 0
        self._conn.executescript(_SCHEMA)
        if fresh:
            # _SCHEMA creates the current shape outright, so nothing to replay.
            self._set_schema_version(SCHEMA_VERSION)
        else:
            self._apply_migrations()
        # After migrations: see the comment on `_INDEXES`.
        self._conn.executescript(_INDEXES)
        self._conn.commit()

    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _set_schema_version(self, version: int) -> None:
        # PRAGMA does not accept bound parameters; the int cast is the guard.
        self._conn.execute(f"PRAGMA user_version = {int(version)}")
        self._conn.commit()

    def _columns_of(self, table: str) -> set[str]:
        return {r["name"] for r in
                self._conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _apply_migration_sql(self, sql: str) -> None:
        """Run one migration, skipping ADD COLUMN steps already applied.

        `executescript` issues an implicit COMMIT and is NOT atomic, and
        `user_version` is only stamped after the whole script - so an
        interruption part-way through (Ctrl-C, a locked database, a full disk)
        leaves the database with SOME of the columns and the OLD version.
        Replaying the script then dies on `duplicate column name` forever,
        and `init_schema` is the first thing both CLIs do, so every command
        including `--status` goes down with it. SQLite has no
        `ADD COLUMN IF NOT EXISTS`, so the check is done here.
        """
        for statement in sql.split(";"):
            statement = statement.strip()
            if not statement:
                continue
            add_column = re.match(
                r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
                statement, re.I)
            if add_column:
                table, column = add_column.group(1), add_column.group(2)
                if column in self._columns_of(table):
                    continue              # already applied, before the crash
            self._conn.execute(statement)

    def _apply_migrations(self) -> None:
        current = self.schema_version()
        for version, sql in _MIGRATIONS:
            if version <= current:
                continue                      # already applied
            if sql.strip():
                self._apply_migration_sql(sql)
            self._set_schema_version(version)


    # ---- two-axis fit (slice 3 amendment) ----

    def save_role_verdict(self, posting_id: str, verdict, *, model: str,
                          prompt_sha: str, now: str | None = None) -> None:
        """Store one role verdict. Does NOT touch `status`.

        Slice 2 owns `status` and the original matching path writes `scored`
        into it; this axis deliberately writes neither, so a cut-over decision
        stays reversible and slice 2's readers see nothing new.
        """
        self._conn.execute(
            "UPDATE postings SET role_title = ?, role_fit = ?, role_discipline = ?,"
            " hybrid_test = ?, hybrid_evidence = ?, role_overlaps = ?,"
            " role_gaps = ?, role_reason = ?, seniority_note = ?,"
            " domain_fit = ?, domain_evidence = ?,"
            " location = COALESCE(?, location), salary = COALESCE(?, salary),"
            " role_scored_at = ?, role_model = ?, role_prompt_sha = ?"
            " WHERE id = ?",
            (verdict.role_title, verdict.role_fit, verdict.discipline,
             verdict.hybrid_test,
             json.dumps(verdict.hybrid_evidence), json.dumps(verdict.overlaps),
             json.dumps(verdict.gaps), verdict.reason, verdict.seniority_note,
             verdict.domain_fit, json.dumps(verdict.domain_evidence),
             # COALESCE, not a plain set: both scoring paths write these two
             # columns, and a null from one must not erase the other's value.
             verdict.location, verdict.salary,
             now or _now_iso(), model, prompt_sha, posting_id))
        self._conn.commit()

    def role_verdict_of(self, posting_id: str):
        from ..matching.models import RoleVerdict
        row = self._conn.execute(
            "SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        if row is None or row["role_fit"] is None:
            return None
        return RoleVerdict(
            role_title=row["role_title"] or "unknown",
            discipline=row["role_discipline"], reason=row["role_reason"],
            hybrid_test=row["hybrid_test"],
            hybrid_evidence=json.loads(row["hybrid_evidence"] or "[]"),
            domain_fit=row["domain_fit"] or "none",
            domain_evidence=json.loads(row["domain_evidence"] or "[]"),
            overlaps=json.loads(row["role_overlaps"] or "[]"),
            gaps=json.loads(row["role_gaps"] or "[]"),
            role_fit=row["role_fit"], seniority_note=row["seniority_note"],
            location=row["location"], salary=row["salary"])

    def save_company_verdict(self, normalised: str, company: str, verdict, *,
                             model: str, prompt_sha: str,
                             now: str | None = None) -> None:
        self._conn.execute(
            "INSERT INTO companies (company_normalised, company, company_fit,"
            " domain_pull, designs_how_things_work, design_valued, stage_fit,"
            " reachability, disqualifier, reason, scored_at, model, prompt_sha)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(company_normalised) DO UPDATE SET"
            " company=excluded.company, company_fit=excluded.company_fit,"
            " domain_pull=excluded.domain_pull,"
            " designs_how_things_work=excluded.designs_how_things_work,"
            " design_valued=excluded.design_valued,"
            " stage_fit=excluded.stage_fit, reachability=excluded.reachability,"
            " disqualifier=excluded.disqualifier, reason=excluded.reason,"
            " scored_at=excluded.scored_at, model=excluded.model,"
            " prompt_sha=excluded.prompt_sha",
            (normalised, company, verdict.company_fit, verdict.domain_pull,
             verdict.designs_how_things_work, verdict.design_valued,
             verdict.stage_fit, verdict.reachability, verdict.disqualifier,
             verdict.reason, now or _now_iso(), model, prompt_sha))
        self._conn.commit()

    def company_verdict_of(self, normalised: str):
        """The stored rubric-B verdict, or None if the company has none.

        A registry row seeded by `set_ats_token` is NOT a verdict - see
        `SCORED_SQL`. Returning its 'neutral'/'not yet scored' placeholder
        would hand every caller a judgement nobody made.
        """
        from ..matching.models import CompanyVerdict
        row = self._conn.execute(
            "SELECT * FROM companies WHERE company_normalised = ?",
            (normalised,)).fetchone()
        if row is None or not row["scored_at"]:
            return None
        return CompanyVerdict(
            reason=row["reason"], domain_pull=row["domain_pull"],
            designs_how_things_work=row["designs_how_things_work"],
            design_valued=row["design_valued"], stage_fit=row["stage_fit"],
            reachability=row["reachability"], disqualifier=row["disqualifier"],
            company_fit=row["company_fit"])

    # ---- ATS registry ----

    #: A `companies` row is a rubric-B VERDICT only when `scored_at` is
    #: non-empty. The table doubles as the ATS board registry, and
    #: `set_ats_token` must seed the NOT NULL rubric-B columns with
    #: something - 'neutral', 'not yet scored', `scored_at = ''`. Every
    #: reader that treats that placeholder as a judgement invents one.
    #: The sentinel is `scored_at`, not `prompt_sha`, because `prompt_sha`
    #: answers a different question (is the verdict CURRENT?) and a reader
    #: keyed on it would also discard a real but stale verdict.
    SCORED_SQL = "scored_at != ''"

    def set_ats_token(self, normalised: str, company: str, provider: str,
                      token: str) -> None:
        """Record a company's job board.

        An INSERT ... ON CONFLICT that updates ONLY the four ats_* columns:
        the row is shared with rubric B, and a company verdict costs an API
        call that a token write must never erase.

        The rubric-B columns are NOT NULL, so a registry-first row is seeded
        with 'unknown'/'neutral'. `companies_needing_score` keys on
        `prompt_sha`, which is '' here (not NULL - `prompt_sha` is itself
        NOT NULL, so the brief's literal NULL would violate the column's own
        constraint), so a seeded row never matches a real sha and is still
        scored.
        """
        self._conn.execute(
            "INSERT INTO companies (company_normalised, company, company_fit,"
            " domain_pull, designs_how_things_work, design_valued, stage_fit,"
            " reachability, reason, scored_at, model, prompt_sha,"
            " ats_provider, ats_token)"
            " VALUES (?,?,'neutral','unknown','unknown','unknown','unknown',"
            "         'unknown','not yet scored','', '', '', ?, ?)"
            " ON CONFLICT(company_normalised) DO UPDATE SET"
            "   ats_provider = excluded.ats_provider,"
            "   ats_token    = excluded.ats_token",
            (normalised, company, provider, token))
        self._conn.commit()

    def companies_with_boards(self) -> list[tuple[str, str, str, str]]:
        rows = self._conn.execute(
            "SELECT company_normalised, company, ats_provider, ats_token"
            " FROM companies WHERE ats_token IS NOT NULL"
            " ORDER BY company_normalised").fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    def all_postings(self) -> list[Posting]:
        """Every posting, rejected included. `candidates()` excludes rejected
        rows, and token harvesting must not."""
        rows = self._conn.execute(
            "SELECT * FROM postings ORDER BY id").fetchall()
        return [self._row_to_posting(r) for r in rows]

    def record_board_result(self, normalised: str, *, ok_at: str | None = None,
                            error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE companies SET ats_last_ok = COALESCE(?, ats_last_ok),"
            " ats_last_error = ? WHERE company_normalised = ?",
            (ok_at, error, normalised))
        self._conn.commit()

    def companies_needing_score(self, prompt_sha: str) -> list[str]:
        """Distinct companies among the CANDIDATE postings with no current
        company verdict.

        Rejected postings are excluded: slice 2 already ruled them out, so
        scoring their employers is pure spend. One row per company is the
        whole economy of this axis.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT p.company_normalised FROM postings p"
            " LEFT JOIN companies c"
            "   ON c.company_normalised = p.company_normalised"
            "  AND c.prompt_sha = ?"
            " WHERE p.status != 'rejected' AND c.company_normalised IS NULL",
            (prompt_sha,)).fetchall()
        return [r[0] for r in rows]

    def company_source_text(self, normalised: str) -> str:
        """The company-level source for rubric B: its HN post, and only that.

        **`source = 'hn'` is load-bearing.** Rubric B judges the EMPLOYER,
        and the HN post is the one text in this database that describes one -
        an ATS row describes an opening. This query was written when a
        company had exactly one posting; slice 6 gave companies ~25 ATS roles
        each at a median 8,412 chars, so without the filter one company's
        rubric-B call would carry ~52K tokens of job adverts, cost dollars
        rather than cents, and on a large board exceed the context window and
        400. A careers page would be the right thing to ADD here, which is
        why the scorer takes this as an injected string.
        """
        rows = self._conn.execute(
            "SELECT company, raw_text FROM postings"
            " WHERE company_normalised = ? AND source = 'hn'"
            " ORDER BY posted_at",
            (normalised,)).fetchall()
        return "\n\n---\n\n".join(r["raw_text"] for r in rows)

    def postings_needing_role_score(self, prompt_sha: str) -> list[Posting]:
        rows = self._conn.execute(
            "SELECT * FROM postings WHERE status != 'rejected'"
            " AND closed_at IS NULL"
            " AND prefilter_drop IS NULL"
            " AND (reach IS NULL OR reach != 'out_of_area')"
            " AND (role_prompt_sha IS NULL OR role_prompt_sha != ?)"
            " ORDER BY posted_at", (prompt_sha,)).fetchall()
        return [self._row_to_posting(r) for r in rows]

    def postings_eligible_for_prefilter(self) -> list[Posting]:
        """Every LIVE row the prefilter should re-examine, dropped ones included.

        Deliberately not `postings_needing_role_score`: that query filters
        `prefilter_drop IS NULL`, so feeding the audit from it excludes
        exactly the rows the audit exists to reconsider, and a `None` reason
        can then only clear a drop on a row that is not dropped. Loosening
        `titles.py` and re-running - the remedy CLAUDE.md, the slice-7 spec
        and `--show-dropped` all prescribe - was a silent no-op.

        It also carries no `role_prompt_sha` clause, for the same reason one
        clause too many caused that bug: a row scored under the CURRENT
        fingerprint would be frozen out of the audit, so reverting a rubric
        could strand a drop that loosening `titles.py` could never clear.
        The audit is pure and free - it is `apply_prefilter`, not a scoring
        call - so the widest live set is the right one. The filters that
        remain match `dropped_postings` and `count_prefiltered` exactly, so
        writer and readers count the same universe.
        """
        rows = self._conn.execute(
            "SELECT * FROM postings WHERE status != 'rejected'"
            " AND closed_at IS NULL").fetchall()
        return [self._row_to_posting(r) for r in rows]

    # ---- prefilter (slice 7) ----

    def apply_prefilter(self, reasons: dict[str, str | None]) -> int:
        """Record each role's prefilter decision. Returns how many were dropped.

        A None reason CLEARS any previous drop, so loosening the filter and
        re-running un-drops automatically and there is no stale exclusion.
        """
        self._conn.executemany(
            "UPDATE postings SET prefilter_drop = ? WHERE id = ?",
            [(reason, pid) for pid, reason in reasons.items()])
        self._conn.commit()
        return sum(1 for r in reasons.values() if r)

    def dropped_postings(self) -> list[tuple[str, str, str]]:
        rows = self._conn.execute(
            "SELECT id, COALESCE(role_title, '(no title)'), prefilter_drop"
            " FROM postings WHERE prefilter_drop IS NOT NULL"
            " AND closed_at IS NULL AND status != 'rejected'"
            " ORDER BY prefilter_drop, company").fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def count_prefiltered(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM postings"
            " WHERE prefilter_drop IS NOT NULL"
            " AND closed_at IS NULL AND status != 'rejected'").fetchone()[0]

    # ---- reachability ----

    def apply_reachability(self, values: dict[str, str]) -> int:
        """Record each role's reach. Returns how many are `out_of_area`.

        Mirrors `apply_prefilter`: the decision is STORED rather than
        computed-and-forgotten, so it is auditable, and a changed value
        overwrites the old one so widening the home area un-excludes
        automatically. Nothing is deleted and `status` is never touched.
        """
        if not values:
            return 0
        self._conn.executemany(
            "UPDATE postings SET reach = ? WHERE id = ?",
            [(v, k) for k, v in values.items()])
        self._conn.commit()
        return sum(1 for v in values.values() if v == "out_of_area")

    def role_locations(self, posting_ids: list[str]
                       ) -> dict[str, tuple[str | None, str | None]]:
        """`(location, workplace_type)` keyed by id.

        These are COLUMNS, not `Posting` fields - the model carries neither,
        so a caller must come through here. Mirrors `role_titles`.
        """
        if not posting_ids:
            return {}
        marks = ",".join("?" * len(posting_ids))
        rows = self._conn.execute(
            f"SELECT id, location, workplace_type FROM postings"
            f" WHERE id IN ({marks})", posting_ids).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    def reach_counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self._conn.execute(
            "SELECT reach, COUNT(*) FROM postings WHERE reach IS NOT NULL"
            " AND closed_at IS NULL AND status != 'rejected' GROUP BY reach")}

    def roles_by_reach(self, reach: str) -> list[tuple[str, str, str]]:
        rows = self._conn.execute(
            "SELECT id, COALESCE(role_title, '(no title)'),"
            " COALESCE(location, '(no location)') FROM postings"
            " WHERE reach = ? AND closed_at IS NULL AND status != 'rejected'"
            " ORDER BY company", (reach,)).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def role_titles(self, posting_ids: list[str]) -> dict[str, str | None]:
        """`role_title` keyed by id, for the given ids.

        NOTE the column is dual-purpose: the ATS insert writes the board's
        own title, and `save_role_verdict` later OVERWRITES it with the
        model's normalised title (an ATS re-poll does not restore it).

        So the prefilter and `--title-match` do NOT only ever see board
        titles. A row re-enters the candidate set whenever its
        `role_prompt_sha` merely differs from the current one, which any
        rubric, profile or model change causes wholesale - and it comes back
        carrying the MODEL's title. Measured 2026-10-02: 63 of 345 live drops
        sit on rows that had already been scored, so those drops were decided
        from a model title, not a board title. A tier can therefore match a
        renamed title, and a role can be dropped on a token its board never
        used. HN rows carry NULL until something scores them.

        `Posting` does not carry `role_title` (`_COLUMNS` excludes it), so the
        prefilter reads it here rather than off a `Posting` attribute that
        does not exist.
        """
        if not posting_ids:
            return {}
        rows = self._conn.execute(
            "SELECT id, role_title FROM postings WHERE id IN"
            f" ({','.join('?' * len(posting_ids))})", posting_ids).fetchall()
        return {r["id"]: r["role_title"] for r in rows}

    def refresh_actions(self) -> int:
        """Recompute `action` for every posting that has both axes.

        Derived in Python rather than in SQL so the matrix has exactly one
        definition - `fit.ACTION` - and a CASE expression cannot drift from it.
        """
        from ..matching.fit import derive_action
        # The join condition carries `SCORED_SQL`, so a SEEDED registry row
        # leaves `company_fit` NULL and the row keeps no action at all. Its
        # placeholder 'neutral' would otherwise pair with a strong role to
        # mint `apply` for a company nobody judged - and `--show-actions`
        # would display that forever. Same principle as the v4 comment on
        # `postings.action`: a placeholder reads as a decision not made.
        rows = self._conn.execute(
            "SELECT p.id, p.role_fit, c.company_fit FROM postings p"
            " LEFT JOIN companies c"
            "   ON c.company_normalised = p.company_normalised"
            f"  AND c.{self.SCORED_SQL}").fetchall()
        updated = 0
        for row in rows:
            action = (derive_action(row["role_fit"], row["company_fit"])
                      if row["role_fit"] and row["company_fit"] else None)
            self._conn.execute("UPDATE postings SET action = ? WHERE id = ?",
                               (action, row["id"]))
            updated += action is not None
        self._conn.commit()
        return updated

    def company_name_of(self, normalised: str) -> str | None:
        row = self._conn.execute(
            "SELECT company FROM postings WHERE company_normalised = ? LIMIT 1",
            (normalised,)).fetchone()
        return row["company"] if row else None

    def count_companies(self) -> int:
        """How many companies rubric B has actually judged.

        NOT `COUNT(*) FROM companies`: the table doubles as the ATS registry,
        so a bare count reported "companies scored: 42" when the answer was 0
        and every one of them was a token harvested from an HN post.
        """
        return self._conn.execute(
            f"SELECT COUNT(*) FROM companies WHERE {self.SCORED_SQL}"
        ).fetchone()[0]

    def action_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT action, COUNT(*) AS n FROM postings"
            " WHERE action IS NOT NULL AND closed_at IS NULL"
            " GROUP BY action ORDER BY n DESC"
        ).fetchall()
        return {r["action"]: r["n"] for r in rows}

    def action_of(self, posting_id: str) -> str | None:
        row = self._conn.execute("SELECT action FROM postings WHERE id = ?",
                                 (posting_id,)).fetchone()
        return row["action"] if row else None

    def by_action(self, action: str) -> list[Posting]:
        rows = self._conn.execute(
            "SELECT * FROM postings WHERE action = ? AND closed_at IS NULL"
            " ORDER BY posted_at DESC", (action,)).fetchall()
        return [self._row_to_posting(r) for r in rows]

    # ---- postings ----

    #: The statuses slice 2 writes, and therefore the only ones it may
    #: overwrite. Anything else was set by a later slice.
    OWN_STATUSES = frozenset({"new", "rejected"})

    def upsert_posting(self, posting: Posting) -> bool:
        """Insert, or refresh the mutable fields of an existing row.

        Returns True when the row is new.

        `first_seen` is immutable in all cases.

        `status` and `reject_reason` are refreshed **only when the existing
        status is one slice 2 itself wrote** (`new` or `rejected`). Slices 3-5
        own the rest of the vocabulary, and re-polling must not reset a
        decision they made.

        Refreshing slice 2's own status is not a nicety. Without it a row
        first seen as "Onsite NYC" keeps `status=rejected,
        reject_reason=not_remote` forever after it is edited to "Remote US" -
        a row whose stated reason contradicts its own `is_remote` and
        `geo_signal` columns, invisible to `candidates()`. More importantly,
        it is the only way a CORRECTED FILTER can ever be applied to rows
        already stored: without it, a filter fix reclassifies nothing in the
        existing database.
        """
        row = self._conn.execute(
            "SELECT status FROM postings WHERE id = ?", (posting.id,)).fetchone()
        if row:
            if row["status"] in self.OWN_STATUSES:
                self._conn.execute(
                    "UPDATE postings SET last_seen = ?, raw_text = ?, company = ?,"
                    " company_normalised = ?, apply_url = ?, is_remote = ?,"
                    " geo_signal = ?, status = ?, reject_reason = ?"
                    " WHERE id = ?",
                    (posting.last_seen, posting.raw_text, posting.company,
                     posting.company_normalised, posting.apply_url,
                     int(posting.is_remote), posting.geo_signal,
                     posting.status, posting.reject_reason, posting.id))
            else:
                self._conn.execute(
                    "UPDATE postings SET last_seen = ?, raw_text = ?, company = ?,"
                    " company_normalised = ?, apply_url = ?, is_remote = ?,"
                    " geo_signal = ? WHERE id = ?",
                    (posting.last_seen, posting.raw_text, posting.company,
                     posting.company_normalised, posting.apply_url,
                     int(posting.is_remote), posting.geo_signal, posting.id))
            self._conn.commit()
            return False

        values = [getattr(posting, c) for c in _INSERT_COLUMNS]
        values[_INSERT_COLUMNS.index("is_remote")] = int(posting.is_remote)
        self._conn.execute(
            f"INSERT INTO postings ({','.join(_INSERT_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(_INSERT_COLUMNS))})", values)
        self._conn.commit()
        return True

    def _row_to_posting(self, row: sqlite3.Row) -> Posting:
        data = {c: row[c] for c in _COLUMNS}
        data["is_remote"] = bool(data["is_remote"])
        return Posting(**data)

    def get(self, posting_id: str) -> Posting | None:
        row = self._conn.execute(
            "SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        return self._row_to_posting(row) if row else None

    def set_status(self, posting_id: str, status: str) -> None:
        """Used by later slices, and by tests proving upsert does not clobber."""
        self._conn.execute("UPDATE postings SET status = ? WHERE id = ?",
                           (status, posting_id))
        self._conn.commit()

    def count_postings(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]

    def count_open_ats_roles(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM postings"
            " WHERE source != 'hn' AND closed_at IS NULL").fetchone()[0]

    def times_seen_company(self, company_normalised: str) -> int:
        """Derived at read time, so it cannot go stale."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM postings WHERE company_normalised = ?",
            (company_normalised,)).fetchone()[0]

    def rejections(self) -> list[Posting]:
        rows = self._conn.execute(
            "SELECT * FROM postings WHERE status = 'rejected' "
            "ORDER BY reject_reason, company").fetchall()
        return [self._row_to_posting(r) for r in rows]

    def candidates(self) -> list[Posting]:
        """Postings not rejected by discovery. This INCLUDES scored postings — it is
        not 'unprocessed'. Use `unscored()` for that."""
        rows = self._conn.execute(
            "SELECT * FROM postings WHERE status != 'rejected'"
            " AND closed_at IS NULL ORDER BY posted_at DESC, company").fetchall()
        return [self._row_to_posting(r) for r in rows]

    def save_verdict(self, posting_id: str, verdict, *, model: str,
                     profile_sha: str, matched_at: str) -> None:
        """Store a match verdict. Sets status to 'scored' ONLY if the current
        status is one slice 3 owns; a status set by a later slice is preserved."""
        self._conn.execute(
            "UPDATE postings SET role_title=?, discipline=?, hybrid_signal=?,"
            " match_reason=?, match_concern=?, fit_score=?, match_bucket=?,"
            " location=?, salary=?, matched_at=?, match_model=?,"
            " match_profile_sha=?,"
            " status = CASE WHEN status IN ('new','scored') THEN 'scored' ELSE status END"
            " WHERE id=?",
            (verdict.role_title, verdict.discipline, verdict.hybrid_signal,
             verdict.reason, verdict.concern, verdict.fit_score, verdict.bucket,
             verdict.location, verdict.salary, matched_at, model, profile_sha,
             posting_id))
        self._conn.commit()

    def verdict_of(self, posting_id: str):
        from ..matching.models import MatchVerdict
        row = self._conn.execute(
            "SELECT role_title, discipline, hybrid_signal, match_reason,"
            " match_concern, fit_score, match_bucket, location, salary"
            " FROM postings WHERE id=?", (posting_id,)).fetchone()
        if not row or row["match_bucket"] is None:
            return None
        return MatchVerdict(
            role_title=row["role_title"], discipline=row["discipline"],
            hybrid_signal=row["hybrid_signal"], reason=row["match_reason"],
            concern=row["match_concern"], fit_score=row["fit_score"],
            bucket=row["match_bucket"], location=row["location"],
            salary=row["salary"])

    def unscored(self) -> list:
        """HN postings discovery kept that have no verdict yet.

        What the ORIGINAL single-axis matcher consumes, and `source = 'hn'`
        is part of that meaning. ATS rows are inserted `status='new'` and the
        two-axis path deliberately never writes `status`, so without the
        filter every ATS role stays "unscored" forever and the bare
        `python -m jobhunt.matching` - documented as "score every unscored
        posting" - would spend ~$29 putting ~1,039 ATS roles through the
        single-axis rubric this slice exists to retire. The two-axis path
        uses `postings_needing_role_score`, which has no source filter
        because the ATS board IS its source.
        """
        rows = self._conn.execute(
            "SELECT * FROM postings WHERE status = 'new'"
            " AND source = 'hn' AND closed_at IS NULL"
            " ORDER BY posted_at DESC, id"
        ).fetchall()
        return [self._row_to_posting(r) for r in rows]

    def scored(self, bucket: str | None = None) -> list:
        sql = ("SELECT * FROM postings WHERE match_bucket IS NOT NULL"
               + (" AND match_bucket = ?" if bucket else "")
               + " ORDER BY fit_score DESC, id")
        rows = (self._conn.execute(sql, (bucket,)) if bucket
                else self._conn.execute(sql)).fetchall()
        return [self._row_to_posting(r) for r in rows]

    def stale_verdict_ids(self, profile_sha: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT id FROM postings WHERE match_bucket IS NOT NULL"
            # `NULL != 'x'` is NULL in SQLite, not true, so a verdict with no
            # recorded fingerprint would be permanently non-stale and never
            # re-scored. Unreachable via save_verdict today; cheap insurance.
            " AND (match_profile_sha IS NULL OR match_profile_sha != ?)"
            " ORDER BY id", (profile_sha,)).fetchall()
        return [r["id"] for r in rows]

    def stale_postings(self, as_of: str, days: int = STALE_DAYS) -> list[Posting]:
        """HN postings older than `days`.

        HN threads are immutable, so a filled role is not knowable there and
        age is the only honest signal. **ATS rows are excluded** (spec
        section 6: the heuristic "stays for HN rows"): their closure is known
        for real from the board, and their `posted_at` comes from
        Greenhouse's `updated_at` / Ashby's `publishedAt`, which are
        routinely months old on roles that are demonstrably still listed - so
        without the filter `discovery --status` would report hundreds of open
        roles as stale.
        """
        cutoff = (date.fromisoformat(as_of) - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM postings WHERE posted_at < ? AND source = 'hn'"
            " ORDER BY posted_at", (cutoff,)).fetchall()
        return [self._row_to_posting(r) for r in rows]

    # ---- ATS roles ----

    def upsert_ats_role(self, role, *, now: str) -> bool:
        """Insert or refresh one ATS role. Returns True when it is new.

        `first_seen` is immutable; `last_seen` always advances; `closed_at` is
        cleared, because a role that is back on the board is open again.
        `status` is only set on insert, so a verdict-bearing status set by a
        later slice survives a re-poll.

        Raises `ValueError` if the role is not storable (foreign location, or
        body too short) rather than returning False - False means "stored but
        not new", and overloading it with "refused" would silently corrupt a
        caller's new-row count. Nothing else enforces that `is_storable` ran
        before this is called, so it is enforced here.
        """
        from .ats_filters import geo_signal_for, is_storable
        from ..normalize import normalize_company

        if not is_storable(role):
            geo = geo_signal_for(role.location)
            reason = ("foreign location" if geo == "non_us"
                       else f"body too short ({len(role.body or '')} chars)")
            raise ValueError(f"{role.id} is not storable: {reason}")

        geo = geo_signal_for(role.location)
        raw = f"{role.title}\n{role.company} | {role.location}\n\n{role.body}"
        existing = self._conn.execute(
            "SELECT id FROM postings WHERE id = ?", (role.id,)).fetchone()
        if existing is None:
            self._conn.execute(
                "INSERT INTO postings (id, source, source_thread_id, company,"
                " company_normalised, raw_text, apply_url, is_remote,"
                " geo_signal, posted_at, first_seen, last_seen, status,"
                " role_title, location, workplace_type, department)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?)",
                (role.id, role.provider, role.token, role.company,
                 normalize_company(role.company), raw, role.apply_url,
                 int(role.workplace_type == "remote"),
                 "us" if geo == "us" else "unknown", role.posted_at, now, now,
                 role.title, role.location, role.workplace_type,
                 role.department))
            self._conn.commit()
            return True
        self._conn.execute(
            "UPDATE postings SET raw_text = ?, apply_url = ?, last_seen = ?,"
            " location = ?, workplace_type = ?, department = ?,"
            " geo_signal = ?, is_remote = ?,"
            " closed_at = NULL WHERE id = ?",
            (raw, role.apply_url, now, role.location, role.workplace_type,
             role.department, "us" if geo == "us" else "unknown",
             int(role.workplace_type == "remote"), role.id))
        self._conn.commit()
        return False

    def purge_unstorable_ats_roles(self, *, dry_run: bool = False) -> list[str]:
        """Delete stored non-HN roles whose location NOW classifies `non_us`.

        Exists because a geography-pattern fix reclassifies nothing on its
        own: `is_storable` only gates what a poll stores going FORWARD, so a
        role already sitting in the database under an older, looser pattern
        stays there - with a stale `geo_signal` - forever. `reconcile_board`
        does not help either; it is fed the board's raw ids, so a role the
        board still lists still looks present.

        Two guards, both load-bearing and both tested:

        - `source = 'hn'` rows are NEVER considered. They are the only
          hand-labelled data in this project.
        - A row carrying a verdict (`matched_at` or `role_scored_at` set) is
          NEVER deleted, even if its location now reads `non_us`. Someone
          paid for that judgement; it is reported, not destroyed.

        Returns the ids that were deleted (or, under `dry_run=True`, the ids
        that WOULD be deleted - nothing is touched in that case).
        """
        from .ats_filters import geo_signal_for

        rows = self._conn.execute(
            "SELECT id, location FROM postings WHERE source != 'hn'"
            " AND matched_at IS NULL AND role_scored_at IS NULL").fetchall()
        ids = [r["id"] for r in rows
               if geo_signal_for(r["location"]) == "non_us"]
        if ids and not dry_run:
            self._conn.executemany(
                "DELETE FROM postings WHERE id = ?", [(i,) for i in ids])
            self._conn.commit()
        return ids

    def get_closed_at(self, posting_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT closed_at FROM postings WHERE id = ?",
            (posting_id,)).fetchone()
        return row["closed_at"] if row else None

    def mark_closed(self, posting_ids, *, now: str) -> int:
        ids = list(posting_ids)
        self._conn.executemany(
            "UPDATE postings SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
            [(now, pid) for pid in ids])
        self._conn.commit()
        return len(ids)

    def open_role_ids_for_board(self, provider: str, token: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT id FROM postings WHERE source = ? AND source_thread_id = ?"
            " AND closed_at IS NULL", (provider, token)).fetchall()
        return {r["id"] for r in rows}

    def reconcile_board(self, provider: str, token: str, seen_ids,
                        *, now: str) -> int:
        """Close stored roles this board no longer lists. Returns how many.

        **An empty `seen_ids` closes NOTHING.** A board that returns zero
        roles is an outage, a renamed token or a moved board far more often
        than it is a company that closed every requisition at once, and
        reading it as mass closure would wipe the pipeline silently while the
        run reported success. Closure is inferred only from a board that came
        back with at least one role.
        """
        seen = set(seen_ids)
        if not seen:
            return 0
        vanished = self.open_role_ids_for_board(provider, token) - seen
        if not vanished:
            return 0
        # `AND closed_at IS NULL` keeps the FIRST date it was seen gone. It is
        # redundant TODAY - `vanished` only ever contains ids that came back
        # from `open_role_ids_for_board`, whose own SELECT already filters
        # `closed_at IS NULL`, so an already-closed id can never reach this
        # UPDATE. Kept anyway as defence-in-depth: if that filter is ever
        # removed, this clause is what still stops a re-poll from moving the
        # closure date forward. Deliberately untested for exactly that reason
        # - the branch is unreachable via any public path today, and a test
        # that can only be reached by reaching into internals would be
        # testing itself, not this code. Do not delete this comment as noise
        # and do not spend a round trying to cover it.
        self._conn.executemany(
            "UPDATE postings SET closed_at = ?"
            " WHERE id = ? AND closed_at IS NULL",
            [(now, pid) for pid in sorted(vanished)])
        self._conn.commit()
        return len(vanished)

    # ---- runs ----

    #: A run row's owner. Every query below filters on it, so a matching run
    #: can never answer a question asked about discovery, or vice versa.
    DISCOVERY = "discovery"
    MATCH = "match"
    ATS = "ats"

    def start_run(self, started_at: str, kind: str = DISCOVERY) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (started_at, kind) VALUES (?, ?)",
            (started_at, kind))
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, finished_at: str,
                   thread_id: str | None = None, posts_seen: int = 0,
                   candidates: int = 0, rejected: int = 0,
                   new_postings: int = 0, error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at = ?, thread_id = ?, posts_seen = ?,"
            " candidates = ?, rejected = ?, new_postings = ?, error = ?"
            " WHERE id = ?",
            (finished_at, thread_id, posts_seen, candidates, rejected,
             new_postings, error, run_id))
        self._conn.commit()

    def _row_to_run(self, row: sqlite3.Row) -> DiscoveryRun:
        return DiscoveryRun(
            id=row["id"], started_at=row["started_at"],
            finished_at=row["finished_at"], thread_id=row["thread_id"],
            posts_seen=row["posts_seen"], candidates=row["candidates"],
            rejected=row["rejected"], new_postings=row["new_postings"],
            error=row["error"])

    def last_run(self, kind: str = DISCOVERY) -> DiscoveryRun | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (kind,)).fetchone()
        return self._row_to_run(row) if row else None

    def failed_runs_since(self, run_id: int | None = None,
                          kind: str = DISCOVERY) -> int:
        """Runs recorded after `run_id` that did not succeed.

        `run_id` is normally the last successful run's id; None means there has
        never been a success, so every recorded run counts. A run with no
        `error` but no `finished_at` counts too - it died before it could
        report anything, which is exactly the silent failure the heartbeat
        exists to surface.
        """
        sql = ("SELECT COUNT(*) FROM runs"
               " WHERE kind = ? AND (error IS NOT NULL OR finished_at IS NULL)")
        if run_id is None:
            return self._conn.execute(sql, (kind,)).fetchone()[0]
        return self._conn.execute(sql + " AND id > ?",
                                  (kind, run_id)).fetchone()[0]

    def last_successful_run(self, kind: str = DISCOVERY) -> DiscoveryRun | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE kind = ?"
            " AND error IS NULL AND finished_at IS NOT NULL"
            " ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        return self._row_to_run(row) if row else None
