from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic

from .. import config
from ..discovery.store import Store
from ..env import EnvFileError, load_env
from .evaluate import evaluate, load_labels, passed, render_report
from .models import MatchVerdict
from .prompt import build_system, build_user, prompt_sha
from .rubrics import (build_company_system, build_role_system,
                      company_prompt_sha, role_prompt_sha)
from .search_config import load_search_config
from .scorer import DEFAULT_MODEL, Scorer

# Opus 5 pricing (spec section on cost): $5 / $25 per MTok in / out. A cached
# system-block read is a tenth of the input price - that discount is the
# entire reason the system block carries `cache_control`.
INPUT_PER_MTOK = 5.0
OUTPUT_PER_MTOK = 25.0
CACHE_READ_PER_MTOK = INPUT_PER_MTOK / 10


class FatalRunError(RuntimeError):
    """The run cannot usefully continue. Distinct from `ScoreError`, which
    describes ONE posting's failure and must never stop the run (spec 9:
    "131 verdicts beat zero"). Printed once, exit 1."""


class AuthError(FatalRunError):
    """No credentials resolve. An auth failure fails every remaining posting
    identically, so retrying the other 131 is the same cryptic message
    repeated 131 times."""


#: How many consecutive failures with the same ROOT CAUSE abort a run.
#:
#: The auth detection below includes a match on an SDK-private error string.
#: If that wording ever changes, the zero-credentials case silently reverts to
#: the original bug: 132 cryptic failures, "scored 0 posting(s)", exit 0. This
#: guard needs no knowledge of the cause - any fault that fails every posting
#: identically (expired key, bad --model, a schema the model cannot satisfy)
#: stops the run instead of repeating itself 132 times.
CONSECUTIVE_FAILURE_LIMIT = 3

#: Exempt from the streak. Both are transient by nature and the SDK has
#: ALREADY retried them internally before `Scorer.score` ever sees one, so a
#: run of them is bad luck, not a misconfiguration. Spec 9 is explicit that
#: one posting's API failure must not fail the run - "131 verdicts beat zero"
#: - and these are the failures it had in mind.
_TRANSIENT_TYPES = tuple(
    t for t in (getattr(anthropic, name, None)
                for name in ("RateLimitError", "APIConnectionError",
                             "InternalServerError", "APITimeoutError"))
    if t is not None)


def _root_cause(exc: BaseException) -> BaseException:
    """The innermost `__cause__`. `Scorer.score` wraps EVERY API and
    validation failure into `ScoreError`, so keying the streak on the raised
    type collapses it to a single value - "three failures of the same type"
    silently became "three failures of any cause", and three unrelated
    transient faults in a row aborted a run that should have continued."""
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return exc


#: `AuthenticationError` (401) and `PermissionDeniedError` (403) are typed
#: SDK exceptions - both subclass `APIStatusError`, which `Scorer.score`
#: already wraps into `ScoreError` (chaining the original via `from exc`).
_FATAL_AUTH_TYPES = tuple(
    t for t in (getattr(anthropic, name, None)
                for name in ("AuthenticationError", "PermissionDeniedError"))
    if t is not None)


def _is_fatal_auth_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    if isinstance(exc, _FATAL_AUTH_TYPES):
        return True
    # Verified against anthropic 1.8.0 (traceback into
    # anthropic._client._validate_headers): when NO credential resolves at
    # all - no ANTHROPIC_API_KEY, no `ant auth login` profile - the SDK
    # raises a bare TypeError before any network call, not a typed
    # AnthropicError. That is the actual failure mode on a machine with no
    # credentials, so it has to be recognised here too.
    if isinstance(exc, TypeError) and "authentication method" in str(exc).lower():
        return True
    return _is_fatal_auth_error(getattr(exc, "__cause__", None))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_profile(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _build_client(client):
    """Lazy client construction. Only called by a code path that is about to
    make an API call - `--print-prompt`, `--show` and `--status` never reach
    here, so they work on a machine with no credentials at all.

    An unset `ANTHROPIC_API_KEY` does not mean there are no credentials - the
    SDK also resolves `ant auth login`-managed profiles - so construction is
    still attempted rather than pre-emptively refused; only an actual failure
    is turned into a message naming both options.
    """
    if client is not None:
        return client
    try:
        return anthropic.Anthropic()
    except Exception as exc:
        # AuthError, not RuntimeError: `main` catches FatalRunError and prints
        # it as one message with exit 1. Raising a bare RuntimeError here made
        # the single failure the brief named by name the one that escaped as
        # a traceback. Note this path is near-unreachable in anthropic 1.8.0:
        # `Anthropic()` constructs fine with no credentials and the failure
        # arrives at request time, where `_is_fatal_auth_error` catches it.
        raise AuthError(
            "No Anthropic credentials available. Set ANTHROPIC_API_KEY or run "
            f"`ant auth login`. ({exc})") from exc


class _Cost:
    """Accumulates usage across a run and renders a cost estimate."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0

    def add(self, scorer: Scorer) -> None:
        self.input_tokens += scorer.last_input_tokens or 0
        self.output_tokens += scorer.last_output_tokens or 0
        self.cache_read_tokens += scorer.last_cache_read_tokens or 0

    def estimate_usd(self) -> float:
        return (self.input_tokens * INPUT_PER_MTOK
                + self.output_tokens * OUTPUT_PER_MTOK
                + self.cache_read_tokens * CACHE_READ_PER_MTOK) / 1_000_000

    def report(self) -> str:
        return (f"tokens: input={self.input_tokens} output={self.output_tokens} "
                f"cache_read={self.cache_read_tokens}  est. cost ${self.estimate_usd():.4f}")


def _absorb_failure(exc: BaseException, label: str, state: dict) -> None:
    """The failure policy, in one place, for every scorer.

    One item's failure must not fail the run - "131 verdicts beat zero" - but
    two shapes ARE fatal, because they will fail every remaining item
    identically: an authentication error, and the same ROOT CAUSE repeating
    `CONSECUTIVE_FAILURE_LIMIT` times.

    Extracted when the two-axis path arrived. A second copy of this would have
    been a second place to get the auth escalation wrong.
    """
    if _is_fatal_auth_error(exc):
        raise AuthError(
            f"Authentication failed while scoring {label}: {exc}\n"
            "Set ANTHROPIC_API_KEY or run `ant auth login`.") from exc
    print(f"{label}: {exc}", file=sys.stderr)
    state["failed"] = state.get("failed", 0) + 1
    cause = _root_cause(exc)
    if isinstance(cause, _TRANSIENT_TYPES):
        state["streak"], state["streak_kind"] = 0, None
        return
    kind = type(cause).__name__
    run = state.get("streak", 0) + 1 if state.get("streak_kind") == kind else 1
    state["streak"], state["streak_kind"] = run, kind
    if run >= CONSECUTIVE_FAILURE_LIMIT:
        raise FatalRunError(
            f"Aborting: {run} consecutive items failed with the same root "
            f"cause ({kind}). This is failing everything, not just this one - "
            f"fix the cause and re-run; verdicts already stored are kept. "
            f"Last error: {exc}") from exc


def _score_one(scorer: Scorer, system: str, posting, store: Store, *,
               model: str, sha: str, cost: _Cost, state: dict) -> MatchVerdict | None:
    """Score one posting. A per-posting failure is printed to stderr, counted
    in `state["failed"]` and None is returned, so the caller's loop continues -
    EXCEPT an authentication failure, or the same failure repeating
    `CONSECUTIVE_FAILURE_LIMIT` times with the same root cause, either of
    which is fatal to the whole
    run and raised as `FatalRunError`."""
    try:
        verdict = scorer.score(system, posting)
    except Exception as exc:                     # noqa: BLE001 - one posting's
        # failure must never fail the run, whatever shape it takes; Scorer
        # already narrows the exceptions it recognises to ScoreError, but an
        # upstream fault (e.g. a raw client exception) must be caught here too.
        #
        # An authentication failure is categorically different: it will fail
        # EVERY remaining posting identically, so it is raised as fatal
        # rather than swallowed and retried 131 more times.
        _absorb_failure(exc, posting.id, state)
        return None
    state["streak"], state["streak_kind"] = 0, None
    store.save_verdict(posting.id, verdict, model=model, profile_sha=sha,
                       matched_at=_now())
    cost.add(scorer)
    if not state.get("printed_cache") and scorer.last_cache_read_tokens is not None:
        print(f"cache-read tokens: {scorer.last_cache_read_tokens}")
        state["printed_cache"] = True
    return verdict


def _run_scoring(store: Store, run_id: int, fn) -> None:
    """Wrap a scoring pass so `finish_run` fires on success AND on an
    exception that escapes the per-posting guard (e.g. a bug, not a
    per-call API failure) - a run row is written either way."""
    try:
        fn()
    except Exception as exc:
        store.finish_run(run_id, finished_at=_now(), error=f"{type(exc).__name__}: {exc}")
        raise
    store.finish_run(run_id, finished_at=_now())


def _cmd_print_prompt(store: Store, profile: dict) -> int:
    system = build_system(profile)
    print(system)
    postings = store.unscored()
    if postings:
        print(build_user(postings[0]))
    return 0


def _warn_if_stale(store: Store, sha: str) -> int:
    """Spec 9: stored verdicts scored against a different prompt are reported,
    naming the remedy. Without this the whole point of recording a fingerprint
    is lost - the user edits the rubric, re-runs, and nothing says that every
    stored verdict now describes a prompt that no longer exists."""
    stale = store.stale_verdict_ids(sha)
    if stale:
        print(f"WARNING: {len(stale)} stored verdict(s) were scored against a "
              "different profile, rubric or model. Re-run with `--rescore` to "
              "refresh them.", file=sys.stderr)
    return len(stale)



def _cmd_two_axis(store: Store, args, profile: dict, client) -> int:
    """Score both axes, then derive the action.

    Companies first: it is the cheap axis - one call per company rather than
    one per posting - so a broken configuration surfaces after a handful of
    calls instead of after a hundred.
    """
    cfg = load_search_config()
    model = args.model or DEFAULT_MODEL
    role_sha = role_prompt_sha(profile, cfg, model)
    company_sha = company_prompt_sha(cfg, model)
    role_system = build_role_system(profile, cfg)
    company_system = build_company_system(cfg)

    scorer = Scorer(_build_client(client), model=model)
    cost = _Cost()
    state: dict = {}
    run_id = store.start_run(_now(), Store.MATCH)

    from . import titles as title_rules
    # The audit is corpus-wide and includes ALREADY-DROPPED rows, so loosening
    # titles.py and re-running un-drops automatically. Sourcing it from
    # postings_needing_role_score (which filters prefilter_drop IS NULL) made
    # that promise a silent no-op.
    audit = store.postings_eligible_for_prefilter()
    known = store.role_titles([p.id for p in audit])
    dropped = store.apply_prefilter(
        {p.id: title_rules.prefilter_drop(known.get(p.id)) for p in audit})
    from . import geography
    # Corpus-wide, like the prefilter, and from the SAME audit source that
    # includes already-excluded rows - so widening home_metros/home_states and
    # re-running un-excludes automatically. Sourcing this from
    # postings_needing_role_score would make that a silent no-op (the C1 bug).
    #
    # location/workplace_type are COLUMNS, not `Posting` fields - `p.location`
    # raises AttributeError, exactly as `p.role_title` did once before.
    where = store.role_locations([p.id for p in audit])
    out_of_area = store.apply_reachability({
        pid: geography.reachability(loc, wt, cfg.home_metros, cfg.home_states)
        for pid, (loc, wt) in where.items()})
    if out_of_area:
        print(f"out of area: {out_of_area} role(s) currently skipped "
              f"(see --show-reach)")
    postings = store.postings_needing_role_score(role_sha)
    if dropped:
        # NOTE this is "currently dropped", not "newly dropped this run" - the
        # audit now re-examines already-dropped rows (that is what makes
        # un-dropping work), so the number repeats run over run. It counts the
        # same universe as `count_prefiltered` and the dashboard chip.
        print(f"prefiltered: {dropped} role(s) currently skipped "
              f"(see --show-dropped)")
    companies = store.companies_needing_score(company_sha)
    if args.title_match is not None:
        # Run the corpus one tier at a time. Measured over 462 scored ATS
        # roles, a design-titled role hits 31.6% and the unclassified residue
        # hit 0 of 224, so scoring everything at once buys most of its results
        # from the cheapest slice and pays ~$50 for the privilege. The
        # prefilter above has ALREADY run over the whole corpus - the tier
        # narrows what is scored, never what is audited.
        pattern = re.compile(args.title_match, re.I)
        pool = len(postings)
        untitled = sum(1 for p in postings if not known.get(p.id))
        postings = [p for p in postings
                    if known.get(p.id) and pattern.search(known[p.id])]
        # ALWAYS print the denominator. A valid-but-wrong pattern - `desinger`,
        # `designer$`, a `|` or a `\b` eaten by the shell - matches nothing,
        # exits 0 and writes a clean run row, which reads exactly like a tier
        # that is already finished. `0 of 1,348` and `0 of 0` do not.
        print(f"title-match {args.title_match!r}: {len(postings):,} of "
              f"{pool:,} candidate(s) matched")
        if untitled:
            # Structural, not incidental: slice 2 gotcha 7 says HN role titles
            # are deliberately never parsed, so these carry no title until
            # something scores them. No tier can reach them, not even '.'.
            print(f"note: {untitled} candidate(s) have no role title and "
                  f"cannot be reached by --title-match; close out a tiered "
                  f"sweep with one run without the flag")
        if pool and not postings:
            # Only blame the pattern when a titled row existed to match. An
            # all-untitled pool (an HN-only database, or any tier run before
            # the first ATS poll) is not a typo, and saying "check the
            # pattern" would send the user after the wrong thing.
            if pool - untitled:
                print(f"warning: --title-match {args.title_match!r} matched "
                      f"none of {pool - untitled:,} titled candidate(s) - "
                      f"nothing was scored. Check the pattern before "
                      f"concluding this tier is done.", file=sys.stderr)
            else:
                print(f"warning: none of the {pool:,} candidate(s) have a "
                      f"role title, so no tier can match them. Run without "
                      f"--title-match to score them.", file=sys.stderr)
    if args.limit is not None:
        postings = postings[:args.limit]
    if args.limit is not None or args.title_match is not None:
        # Scope BOTH axes to the trial. Narrowing only the postings would still
        # score all 131 companies and cost the full amount for a 5-posting test.
        wanted = {p.company_normalised for p in postings}
        companies = [c for c in companies if c in wanted]
    print(f"companies to score: {len(companies)}   "
          f"postings to score: {len(postings)}")

    scored_companies = 0
    for normalised in companies:
        name = store.company_name_of(normalised) or normalised
        try:
            verdict = scorer.score_company(
                company_system, name, store.company_source_text(normalised))
        except Exception as exc:                 # noqa: BLE001 - _absorb_failure
            _absorb_failure(exc, f"company {name}", state)
            continue
        state["streak"], state["streak_kind"] = 0, None
        cost.add(scorer)
        store.save_company_verdict(normalised, name, verdict, model=model,
                                   prompt_sha=company_sha)
        scored_companies += 1

    scored_roles = 0
    for posting in postings:
        try:
            verdict = scorer.score_role(role_system, posting)
        except Exception as exc:                 # noqa: BLE001
            _absorb_failure(exc, posting.id, state)
            continue
        state["streak"], state["streak_kind"] = 0, None
        cost.add(scorer)
        store.save_role_verdict(posting.id, verdict, model=model,
                                prompt_sha=role_sha)
        scored_roles += 1

    actioned = store.refresh_actions()
    store.finish_run(run_id, finished_at=_now())
    failed = state.get("failed", 0)
    print(f"scored {scored_companies} company/companies and {scored_roles} "
          f"posting(s); {actioned} now carry an action"
          + (f"; {failed} failed" if failed else ""))
    print(cost.report())
    return 0


def _cmd_show_dropped(store: Store) -> int:
    rows = store.dropped_postings()
    print(f"{len(rows)} role(s) skipped by the prefilter\n")
    for _, title, reason in rows:
        print(f"  {title[:60]:60s}  {reason}")
    print("\nNothing here was deleted. Loosen titles.py and re-run "
          "`--two-axis` to un-drop.")
    return 0


def _cmd_show_reach(store: Store, which: str) -> int:
    rows = store.roles_by_reach(which)
    print(f"{len(rows)} role(s) with reach {which!r}")
    for _id, title, location in rows:
        print(f"  {title}  \u2014  {location}")
    return 0


def _cmd_show_actions(store: Store, only: str | None) -> int:
    from .fit import ACTION
    order = ["apply_priority", "apply_and_outreach", "apply", "apply_if_comp",
             "maybe_queue", "outreach_lead", "drop"]
    for action in order:
        if only and action != only:
            continue
        rows = store.by_action(action)
        if not rows:
            continue
        print(f"\n## {action}  ({len(rows)})")

        from .commute import is_commutable
        from .search_config import load_search_config
        metros = load_search_config().home_metros

        def rank(posting):
            verdict = store.role_verdict_of(posting.id)
            loc = (verdict.location if verdict else None) or ""
            # Remote first, then commutable, then everything else. Ordering
            # only - nothing is hidden, because they decide what they will
            # travel for.
            if "remote" in loc.casefold():
                return 0
            return 1 if is_commutable(loc, metros) else 2

        for posting in sorted(rows, key=rank):
            verdict = store.role_verdict_of(posting.id)
            title = verdict.role_title if verdict else "?"
            print(f"  {posting.company} - {title}")
            if verdict:
                print(f"      {verdict.reason}")
    return 0


def _cmd_status(store: Store, args, credentials: str) -> int:
    # The SOURCE of the credential, never its value - `--status` is the
    # one place to answer "which key is this run going to use?", and a
    # test asserts the secret itself never reaches stdout.
    print(f"credentials: {credentials}")
    buckets = {b: len(store.scored(bucket=b)) for b in ("strong", "maybe", "no")}
    print("buckets: " + ", ".join(f"{b}={n}" for b, n in buckets.items()))
    # Labelled explicitly: this is `unscored()`'s count, which is filtered to
    # `source = 'hn'` because it feeds only the retired single-axis scorer.
    # Left as the bare "unscored remaining" it read as the WHOLE backlog, so a
    # user polling ~1,039 ATS roles saw a small HN-only number here and
    # concluded there was nothing left to score.
    print(f"unscored (HN, single-axis) remaining: {len(store.unscored())}")
    profile = None
    try:
        profile = _load_profile(args.profile)
        sha = prompt_sha(profile, args.model or DEFAULT_MODEL)
        print(f"stale verdicts: {_warn_if_stale(store, sha)}")
    except FileNotFoundError:
        print(f"stale verdicts: unknown (no profile at {args.profile})")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        # `prompt_sha` renders the whole prompt, so an incomplete profile now
        # raises where the old profile-dict hash did not. `--status` is
        # read-only and must still report the counts above it.
        print(f"stale verdicts: unknown (profile at {args.profile} is "
              f"unreadable: {type(exc).__name__}: {exc}; re-run "
              f"`python -m jobhunt.ingest`)")

    # The ATS/role-fit backlog - the number that actually matters now that
    # ATS boards are the role source (spec section 8). Computed the same way
    # `--two-axis` computes it. Guarded exactly like the staleness line above,
    # and for the same reason: no credentials, and no/broken profile.json or
    # search.yaml, must still let `--status` finish and print "unknown"
    # rather than crash - it is the one command required to always work.
    try:
        if profile is None:
            raise FileNotFoundError(str(args.profile))
        cfg = load_search_config()
        role_sha = role_prompt_sha(profile, cfg, args.model or DEFAULT_MODEL)
        awaiting = len(store.postings_needing_role_score(role_sha))
        print(f"ATS roles awaiting a role verdict: {awaiting}")
    except FileNotFoundError as exc:
        print("ATS roles awaiting a role verdict: unknown "
              f"(no profile or search config: {exc})")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        print("ATS roles awaiting a role verdict: unknown "
              f"({type(exc).__name__}: {exc})")

    counts = store.reach_counts()
    if counts:
        print("reach: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    two_axis = store.action_counts()
    print(f"companies scored: {store.count_companies()}")
    if two_axis:
        print("actions: " + ", ".join(f"{a}={n}" for a, n in two_axis.items()))
    last = store.last_run(Store.MATCH)
    if last is None:
        print("last run: none")
    else:
        flag = "  (FAILED)" if last.error or not last.finished_at else ""
        print(f"last run: {last.finished_at or last.started_at}{flag}")
    return 0


def _cmd_show(store: Store, bucket: str) -> int:
    rows = store.scored(bucket=bucket)
    print(f"{len(rows)} {bucket} posting(s)")
    for posting in rows:
        verdict = store.verdict_of(posting.id)
        if verdict is None:
            continue
        print(f"[{verdict.fit_score:3d}] {verdict.role_title} — {posting.company}")
        print(f"      reason : {verdict.reason}")
        print(f"      concern: {verdict.concern}")
    return 0


def _cmd_eval(store: Store, args, profile: dict, client) -> int:
    model = args.model or DEFAULT_MODEL
    sha = prompt_sha(profile, model)
    labels = load_labels(config.EVAL_LABELS)
    stale_ids = set(store.stale_verdict_ids(sha))

    verdicts: dict[str, MatchVerdict] = {}
    to_score = []
    for label in labels:
        existing = store.verdict_of(label.id)
        # `--rescore` (with or without `--all`) forces a fresh score for every
        # label. Without it these flags were silently ignored here, so the one
        # command a user would reach for to re-measure did nothing at all.
        if existing is not None and label.id not in stale_ids and not args.rescore:
            verdicts[label.id] = existing
        else:
            to_score.append(label)

    # Printed unconditionally, including when nothing was scored. Silence read
    # as "nothing cost anything", which is indistinguishable from "this report
    # is entirely recycled".
    print(f"labels: {len(labels)}  reused: {len(labels) - len(to_score)}  "
          f"to score: {len(to_score)}")

    if to_score:
        system = build_system(profile)
        client = _build_client(client)
        scorer = Scorer(client, model=model)
        cost = _Cost()
        state: dict = {}
        run_id = store.start_run(_now(), Store.MATCH)

        def _pass() -> None:
            for label in to_score:
                posting = store.get(label.id)
                if posting is None:
                    print(f"{label.id}: not present in this database, skipping",
                          file=sys.stderr)
                    continue
                verdict = _score_one(scorer, system, posting, store, model=scorer.model,
                                     sha=sha, cost=cost, state=state)
                if verdict is not None:
                    verdicts[label.id] = verdict

        _run_scoring(store, run_id, _pass)
        print(cost.report())

    report = evaluate(labels, verdicts)
    print(render_report(report))
    return 0 if passed(report) else 1


def _cmd_score(store: Store, args, profile: dict, client) -> int:
    model = args.model or DEFAULT_MODEL
    sha = prompt_sha(profile, model)

    if args.rescore:
        ids = ([p.id for p in store.scored()] if args.all
               else store.stale_verdict_ids(sha))
        if not ids:
            print("nothing to rescore")
            return 0
        postings = [store.get(i) for i in ids]
    else:
        _warn_if_stale(store, sha)
        postings = store.unscored()
        if args.limit is not None:
            postings = postings[:args.limit]
        if not postings:
            print("nothing to score")
            return 0

    system = build_system(profile)
    client = _build_client(client)
    scorer = Scorer(client, model=model)
    cost = _Cost()
    state: dict = {}
    scored = 0
    run_id = store.start_run(_now(), Store.MATCH)

    def _pass() -> None:
        nonlocal scored
        for posting in postings:
            if posting is None:
                continue
            verdict = _score_one(scorer, system, posting, store, model=scorer.model,
                                 sha=sha, cost=cost, state=state)
            if verdict is not None:
                scored += 1

    try:
        _run_scoring(store, run_id, _pass)
    finally:
        # Printed on the abort path too. A run that stopped after three
        # failures still scored whatever came before them, and the cost of
        # those calls is still real - reporting only on the happy path made
        # an aborted run look like it had done nothing.
        failed = state.get("failed", 0)
        print(f"scored {scored} posting(s)"
              + (f", {failed} failed (see stderr)" if failed else ""))
        if scored:
            print(cost.report())
    return 0


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobhunt.matching",
        description="Score job postings against the profile with Claude.")
    parser.add_argument("--db", type=Path, default=config.JOBS_DB)
    parser.add_argument("--profile", type=Path, default=config.PROFILE_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--title-match", default=None, metavar="REGEX",
                        help="with --two-axis, score only roles whose "
                             "title matches (case-insensitive)")
    parser.add_argument("--print-prompt", action="store_true")
    parser.add_argument("--rescore", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--show", nargs="?", const="strong", default=None,
                        choices=("strong", "maybe", "no"), metavar="BUCKET")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--two-axis", action="store_true",
                        help="score role fit and company fit, then derive the action")
    parser.add_argument("--show-actions", nargs="?", const="",
                        default=None, metavar="ACTION")
    parser.add_argument("--show-dropped", action="store_true",
                        help="list roles the prefilter skipped, and why")
    parser.add_argument("--show-reach", nargs="?", const="out_of_area",
                        default=None, metavar="REACH",
                        help="list roles by reachability (default out_of_area)")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    # Silently-ignored flags are worse than rejected ones: `--limit 5
    # --rescore` looked like a cheap partial re-score and was a full one.
    if args.all and not args.rescore:
        parser.error("--all only means anything with --rescore")
    if args.all and args.eval:
        parser.error("--all means nothing with --eval: `--eval --rescore` "
                     "already re-scores all 25 labelled postings")
    if args.limit is not None and (args.rescore or args.eval):
        parser.error("--limit applies to a normal scoring run, "
                     "not to --rescore or --eval")
    # `--limit 5 --two-axis` would otherwise look like a five-posting trial and
    # be a full ~263-call run. --limit IS honoured by --two-axis; --rescore and
    # --all are not, so they are rejected rather than ignored.
    if args.title_match is not None:
        if not args.two_axis:
            parser.error("--title-match only applies to --two-axis")
        try:
            re.compile(args.title_match, re.I)
        except re.error as exc:
            # A bad pattern must not match nothing and report a clean,
            # zero-cost run - that reads identically to "the tier is done".
            parser.error(f"--title-match is not a valid regex: {exc}")
    if args.two_axis and (args.rescore or args.all):
        parser.error("--two-axis does not support --rescore or --all: edit "
                     "search.yaml or the rubric and re-run, which changes the "
                     "prompt fingerprint and re-scores what it must")

    # A listing command: no credentials, no spend. Anything that would score
    # alongside it is rejected rather than silently ignored.
    if args.show_reach is not None:
        if args.show_reach not in ("remote", "local", "out_of_area", "unknown"):
            parser.error(f"--show-reach: unknown reach {args.show_reach!r} "
                         "(remote, local, out_of_area, unknown)")
        if (args.two_axis or args.rescore or args.eval or args.status
                or args.limit is not None or args.title_match is not None):
            parser.error("--show-reach is a listing command and cannot be "
                         "combined with scoring or status flags")
        store = Store(args.db)
        store.init_schema()
        return _cmd_show_reach(store, args.show_reach)

    # Before anything builds a client. The SDK reads os.environ only, and a
    # scheduled run has no shell profile to export from.
    try:
        credentials = load_env()
    except EnvFileError as exc:
        # A misconfigured path or a typo'd secrets file is a user error.
        # One line and exit 1, matching how every other fatal condition
        # in this CLI reports - a traceback buries the one useful line.
        print(str(exc), file=sys.stderr)
        return 1

    store = Store(args.db)
    store.init_schema()

    if args.print_prompt:
        return _cmd_print_prompt(store, _load_profile(args.profile))
    if args.status:
        return _cmd_status(store, args, credentials)
    if args.show_dropped:
        return _cmd_show_dropped(store)
    if args.show is not None:
        return _cmd_show(store, args.show)
    if args.show_actions is not None:
        return _cmd_show_actions(store, args.show_actions or None)

    profile = _load_profile(args.profile)

    try:
        if args.two_axis:
            return _cmd_two_axis(store, args, profile, client)
        if args.eval:
            return _cmd_eval(store, args, profile, client)
        return _cmd_score(store, args, profile, client)
    except FatalRunError as exc:
        # Fatal to the whole run, not one posting: printed once as a single
        # helpful message, not repeated per posting, and the run must not
        # report success (exit 0) with zero postings scored.
        print(str(exc), file=sys.stderr)
        return 1
