from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from .. import config
from ..normalize import normalize_company, slugify
from . import hn
from .filters import split
from .hn_parse import clean_post_text, parse_post
from .models import Posting
from .store import STALE_DAYS, Store

#: The thread is monthly, so more than 40 days without a successful run means
#: at least one month was missed. The swr /fam cron vanished unnoticed for
#: months; this is the self-reporting guard against the same failure here.
HEARTBEAT_DAYS = 40


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalised_company(company: str, posting_id: str) -> str:
    """`company` is guaranteed non-empty, and that guarantee has to carry
    across to the indexed column slice 5 keys on.

    `normalize_company` strips all punctuation and legal-form tokens, so a
    first segment of "//" normalises to "" - zero real occurrences in the
    September thread, but an empty identity in an indexed column is not
    something to leave to luck. Fall back to a slug of the company, then to
    the posting id, which is always unique and non-empty.
    """
    return normalize_company(company) or slugify(company) or posting_id


def _posting_from(parsed, thread_id: str, posted_at: str, seen: str,
                  status: str, reason: str | None) -> Posting:
    return Posting(
        id=parsed.id, source="hn", source_thread_id=thread_id,
        company=parsed.company,
        company_normalised=_normalised_company(parsed.company, parsed.id),
        raw_text=parsed.raw_text, apply_url=parsed.apply_url,
        is_remote=parsed.is_remote, geo_signal=parsed.geo_signal,
        posted_at=posted_at, first_seen=seen, last_seen=seen,
        status=status, reject_reason=reason)


#: Marks the ONE case in which `raw_text` is not cleaned plain text: cleaning
#: produced nothing at all, so the raw HN markup is stored instead. A consumer
#: can test for this prefix rather than having to sniff for tags.
RAW_MARKUP_PREFIX = "<unparsed raw markup> "


def _parse_failure_posting(item_id: str, raw_text: str, thread_id: str,
                           posted_at: str, seen: str) -> Posting:
    """A post that could not be parsed at all is still recorded, never
    silently dropped. `company` is deliberately an obvious sentinel, not a
    guess - there is nothing dependable to derive it from.

    `raw_text` keeps ONE format wherever possible. Parsing fails for two
    different reasons, and only one of them costs us the cleaned text:

    * cleaning worked and only company extraction failed -> store the CLEANED
      text, exactly like every other row, so slice 3 reads one format.
    * cleaning produced nothing -> there is no cleaned text to store, so store
      the raw markup, prefixed with RAW_MARKUP_PREFIX so a consumer can tell.

    Previously every parse failure stored raw markup (verified: '<p>   <p> '),
    giving `raw_text` two incompatible formats keyed on `status` - and spec
    section 8 has slice 3 reading `raw_text`.

    `clean_post_text` cannot raise here: `parse_post` already ran it on this
    same text and got far enough to raise a ValueError afterwards.
    """
    cleaned = clean_post_text(raw_text)
    text = cleaned if cleaned else f"{RAW_MARKUP_PREFIX}{raw_text}"
    return Posting(
        id=f"hn:{item_id}", source="hn", source_thread_id=thread_id,
        company="<unparsed>",
        company_normalised=_normalised_company("<unparsed>", f"hn:{item_id}"),
        raw_text=text, apply_url=None, is_remote=False, geo_signal="unknown",
        posted_at=posted_at, first_seen=seen, last_seen=seen,
        status="rejected", reject_reason="parse_error")


def _month_of(created_at: str) -> str:
    """The thread's month, as YYYY-MM-01."""
    return created_at[:7] + "-01"


def _status_report(store: Store, today: str) -> int:
    """Report the last run AND the last successful run.

    Reporting only the last SUCCESSFUL run makes continuous failure look
    healthy: one success followed by months of failed runs printed a clean
    report and an empty stderr, while `runs.error` was written by `finish_run`
    and read by nothing. The heartbeat's whole justification is a cron that
    failed unnoticed for months, so a report that can assert health during an
    outage is worthless.
    """
    last = store.last_run()
    run = store.last_successful_run()

    if run is None:
        print("No successful run recorded yet. Run `python -m jobhunt.discovery`.")
    else:
        print(f"last successful run : {run.finished_at}")
        print(f"thread              : {run.thread_id}")
        print(f"posts / candidates  : {run.posts_seen} / {run.candidates}")
        print(f"rejected / new      : {run.rejected} / {run.new_postings}")
        print(f"postings stored     : {store.count_postings()}")
        stale = store.stale_postings(as_of=today)
        print(f"stale (>{STALE_DAYS}d)        : {len(stale)}")

    if last is not None:
        print(f"last run attempted  : {last.finished_at or last.started_at}"
              f"{'  (FAILED)' if last.error or not last.finished_at else ''}")

    _warn_if_failing(store, last, run)

    if run is not None:
        age = (date.fromisoformat(today)
               - date.fromisoformat(run.finished_at[:10])).days
        if age > HEARTBEAT_DAYS:
            print(f"\n*** WARNING: last successful run was {age} days ago. "
                  f"The HN thread is monthly, so at least one month has been "
                  f"missed. Check whether the scheduled run is still firing. ***",
                  file=sys.stderr)
    return 0


def _warn_if_failing(store: Store, last, run) -> None:
    """Prominent stderr warning whenever the most recent run is not the most
    recent SUCCESSFUL run."""
    if last is None:
        return                       # fresh database, nothing has run yet
    if run is not None and last.id == run.id:
        return                       # the newest run is the successful one
    failed = store.failed_runs_since(run.id if run else None)
    detail = last.error or "run did not finish; no error was recorded"
    if run is None:
        tail = (f"There has never been a successful run, and {failed} "
                f"run(s) have failed.")
    else:
        tail = (f"{failed} run(s) have failed since the last success at "
                f"{run.finished_at}.")
    print(f"\n*** WARNING: the most recent run FAILED: {detail}\n"
          f"    {tail}\n"
          f"    The figures above describe the last SUCCESSFUL run and do not "
          f"reflect the current state. ***", file=sys.stderr)


def main(argv: list[str] | None = None, fetch=None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobhunt.discovery",
        description="Poll the monthly HN 'Who is hiring?' thread.")
    parser.add_argument("--db", type=Path, default=config.JOBS_DB)
    parser.add_argument("--snapshot-dir", type=Path, default=config.HN_SNAPSHOT_DIR)
    parser.add_argument("--thread", help="poll a specific thread id (backfill)")
    parser.add_argument("--status", action="store_true",
                        help="report the last run and exit")
    parser.add_argument("--show-rejections", action="store_true",
                        help="list rejected postings with their reason and exit")
    parser.add_argument("--today", default=date.today().isoformat(),
                        help="override today's date (testing)")
    args = parser.parse_args(argv)

    fetcher = fetch or hn.httpx_fetcher
    store = Store(args.db)
    store.init_schema()

    if args.status:
        return _status_report(store, args.today)

    if args.show_rejections:
        rows = store.rejections()
        print(f"{len(rows)} rejected postings")
        for r in rows:
            # reject_reason is nullable: a slice-3 set_status(id, "rejected")
            # leaves it NULL, and formatting None with :15s is a TypeError -
            # raised AFTER the count line had already printed.
            reason = r.reject_reason or "-"
            print(f"  [{reason:15s}] {r.company[:40]:42s} {r.id}")
        return 0

    run_id = store.start_run(_now())
    try:
        if args.thread:
            thread_id = args.thread
            ref, raw_posts = hn.fetch_thread_with_meta(
                thread_id, fetcher, snapshot_dir=args.snapshot_dir)
            posted_at = _month_of(ref.created_at)
            ref_title = f"(backfill {thread_id})"
        else:
            ref = hn.find_current_thread(fetcher)
            thread_id, ref_title = ref.id, ref.title
            meta_ref, raw_posts = hn.fetch_thread_with_meta(
                thread_id, fetcher, snapshot_dir=args.snapshot_dir)
            # posted_at comes from the ITEM response on both paths. This
            # path used to take it from the search hit and then discard
            # meta_ref, so the two sources could disagree unnoticed.
            posted_at = _month_of(meta_ref.created_at)

        parsed, parse_failures = [], []
        for raw in raw_posts:
            try:
                parsed.append(parse_post(raw))
            except ValueError as exc:
                parse_failures.append((raw, str(exc)))

        result = split(parsed)
        new_count = 0
        for post in result.candidates:
            if store.upsert_posting(_posting_from(
                    post, thread_id, posted_at, args.today, "new", None)):
                new_count += 1
        for post, reason in result.rejections:
            if store.upsert_posting(_posting_from(
                    post, thread_id, posted_at, args.today, "rejected", reason)):
                new_count += 1
        for raw, _msg in parse_failures:
            if store.upsert_posting(_parse_failure_posting(
                    raw.item_id, raw.text, thread_id, posted_at, args.today)):
                new_count += 1

        # Parse failures ARE stored, with status='rejected' and reason
        # 'parse_error', so they must be counted as rejected. Excluding them
        # broke the accounting invariant posts_seen == candidates + rejected
        # the moment the parse-failure path fired.
        rejected_count = len(result.rejections) + len(parse_failures)
        assert len(raw_posts) == len(result.candidates) + rejected_count, (
            f"run accounting does not balance: posts_seen={len(raw_posts)} != "
            f"candidates={len(result.candidates)} + rejected={rejected_count}")

        from .ats_token import harvest
        boards = harvest(store)
        print(f"companies with an ATS board: {boards}")

        store.finish_run(
            run_id, finished_at=_now(), thread_id=thread_id,
            posts_seen=len(raw_posts), candidates=len(result.candidates),
            rejected=rejected_count, new_postings=new_count)

        print(f"thread {thread_id} {ref_title}")
        print(f"  posts seen : {len(raw_posts)}")
        print(f"  candidates : {len(result.candidates)}")
        print(f"  rejected   : {rejected_count}")
        print(f"  new rows   : {new_count}")
        if parse_failures:
            print(f"  parse failures: {len(parse_failures)}", file=sys.stderr)
            for raw, msg in parse_failures:
                print(f"    {raw.item_id}: {msg}", file=sys.stderr)
        return 0
    except Exception as exc:
        store.finish_run(run_id, finished_at=_now(), error=f"{type(exc).__name__}: {exc}")
        raise
