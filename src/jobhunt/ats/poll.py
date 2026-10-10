"""Poll every registered company job board.

This is the role source. HN still runs, but only to discover which companies
exist - see `discovery.ats_token.harvest`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .. import config
from ..discovery.ats import BoardError, fetch_board, httpx_fetcher
from ..discovery.ats_filters import is_storable
from ..discovery.seed import load_seed, seed_boards
from ..discovery.store import Store


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().date().isoformat()


def _cmd_status(store: Store) -> int:
    """Report the last poll AND the last SUCCESSFUL poll.

    Printing `finished_at` alone made a total outage look healthy: every one
    of the 42 boards can 404 and the run still finishes and still stamps a
    time, so the one line this used to print was indistinguishable from a
    clean poll. That is the same failure `runs.kind` was introduced to
    prevent, and both of the other CLIs already flag it - `discovery
    --status` and `matching --status` each mark the last run FAILED on
    `error or not finished_at`.
    """
    boards = store.companies_with_boards()
    print(f"companies with a board : {len(boards)}")
    print(f"open ATS roles stored  : {store.count_open_ats_roles()}")
    last = store.last_run(Store.ATS)
    ok = store.last_successful_run(Store.ATS)
    if last is None:
        print("last poll              : none")
    else:
        failed = bool(last.error) or not last.finished_at
        print(f"last poll              : "
              f"{last.finished_at or last.started_at}"
              f"{'  (FAILED)' if failed else ''}")
        if failed:
            print(f"  failure              : "
                  f"{last.error or 'poll did not finish; no error recorded'}")
    print(f"last successful poll   : {ok.finished_at if ok else 'none'}")
    return 0


def main(argv: list[str] | None = None, fetch=None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobhunt.ats",
        description="Poll company ATS boards for roles.")
    parser.add_argument("--db", type=Path, default=config.JOBS_DB)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and report, store nothing")
    parser.add_argument("--limit", type=int, default=None,
                        help="poll only the first N boards")
    parser.add_argument("--purge-foreign", action="store_true",
                        help="delete stored roles whose location now "
                             "classifies non-US (default: report only)")
    parser.add_argument("--seed", nargs="?", const="", metavar="FILE",
                        help="register the hand-curated boards "
                             "(default seed_boards.yaml), then exit")
    args = parser.parse_args(argv)

    store = Store(args.db)
    store.init_schema()
    if args.seed is not None:
        boards = load_seed(Path(args.seed) if args.seed else None)
        print(f"seeded {seed_boards(store, boards)} board(s)")
        return 0
    if args.status:
        return _cmd_status(store)

    fetch = fetch or httpx_fetcher
    boards = store.companies_with_boards()
    if args.limit is not None:
        boards = boards[:args.limit]
    print(f"boards to poll: {len(boards)}")

    run_id = store.start_run(_now(), Store.ATS)
    now, raw_total, seen_total, new_total, closed_total, failed = (
        _today(), 0, 0, 0, 0, 0)

    for normalised, company, provider, token in boards:
        try:
            roles = fetch_board(provider, token, company, fetch=fetch,
                                snapshot_dir=config.ATS_SNAPSHOT_DIR)
        except BoardError as exc:
            # One company's board is one company's problem: a token scraped
            # from prose can simply be wrong.
            print(f"  {exc}", file=sys.stderr)
            store.record_board_result(normalised, error=str(exc))
            failed += 1
            continue
        store.record_board_result(normalised, ok_at=now, error=None)
        raw_total += len(roles)
        storable = [r for r in roles if is_storable(r)]
        seen_total += len(storable)
        if args.dry_run:
            continue
        for role in storable:
            new_total += store.upsert_ats_role(role, now=now)
        # RAW ids, not `storable` ids. A role the board still lists but which
        # newly fails `is_storable` - its description was shortened below
        # MIN_BODY_CHARS, or the opening moved abroad - is absent from
        # `storable`, and passing that set would record it as filled. The
        # board is the only authority on what is still listed; `is_storable`
        # only decides what is worth STORING.
        closed_total += store.reconcile_board(
            provider, token, {r.id for r in roles}, now=now)

    rejected_total = raw_total - seen_total
    store.finish_run(run_id, finished_at=_now(), posts_seen=raw_total,
                     candidates=seen_total, rejected=rejected_total,
                     new_postings=new_total,
                     error=f"{failed} board(s) failed" if failed else None)
    print(f"roles seen {seen_total}; new {new_total}; closed {closed_total}"
          + (f"; {failed} board(s) failed" if failed else ""))

    # A geography-pattern fix reclassifies nothing on its own: `is_storable`
    # only gates what a FUTURE poll stores, so rows already on disk under an
    # older pattern sit there forever unless something looks back at them.
    # Deleting is not something a routine poll should do silently, so this
    # only ever REPORTS unless `--purge-foreign` is passed, and it never runs
    # under `--dry-run`, which promises to store nothing.
    if not args.dry_run:
        foreign_ids = store.purge_unstorable_ats_roles(
            dry_run=not args.purge_foreign)
        if foreign_ids:
            verb = ("removed" if args.purge_foreign
                    else "found (not removed; pass --purge-foreign to delete)")
            print(f"foreign roles {verb}: {len(foreign_ids)}")

    return 0
