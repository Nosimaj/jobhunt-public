from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable

import httpx

from .models import HnRawPost, HnThreadRef

#: A fetcher takes a URL and returns parsed JSON. Injected so no test touches
#: the network.
Fetcher = Callable[[str], dict]

# Query by author, not by title: 1,194 stories match "Ask HN: Who is hiring",
# including imitators. HTTPS is required - the http host does not return JSON.
SEARCH_URL = ("https://hn.algolia.com/api/v1/search_by_date"
              "?tags=story,author_whoishiring&hitsPerPage=10")
ITEM_URL = "https://hn.algolia.com/api/v1/items/{thread_id}"

_TIMEOUT = 30.0


def httpx_fetcher(url: str) -> dict:
    """The real network fetcher. Explicit timeout, no retry-forever path.

    Spec section 4 requires a non-JSON response to be reported with BOTH the
    URL and the status. A bare `.json()` raised a JSONDecodeError naming
    neither, and that string is what lands in `runs.error` - the only record
    a later reader has of why a run failed. `http://hn.algolia.com` returning
    HTML instead of JSON is the exact case this is for.
    """
    response = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(
            f"{url} returned HTTP {response.status_code} but the body is not "
            f"JSON: {exc}") from exc


def find_current_thread(fetch: Fetcher = httpx_fetcher) -> HnThreadRef:
    """Newest `Who is hiring?` story by the canonical poster.

    The poster publishes a sibling `Who wants to be hired?` story the same
    minute each month, so the newest hit overall is the wrong one about half
    the time. Filter on the title.
    """
    payload = fetch(SEARCH_URL)
    hits = payload.get("hits") or []
    hiring = [h for h in hits if "Who is hiring" in (h.get("title") or "")]
    if not hiring:
        raise LookupError(
            f"no 'Who is hiring' story found among {len(hits)} hits from {SEARCH_URL}")
    newest = max(hiring, key=lambda h: h.get("created_at") or "")
    return HnThreadRef(id=str(newest["objectID"]),
                       title=newest["title"],
                       created_at=newest["created_at"])


def fetch_thread_with_meta(thread_id: str, fetch: Fetcher = httpx_fetcher,
                           snapshot_dir: Path | None = None
                           ) -> tuple[HnThreadRef, list[HnRawPost]]:
    """Top-level comments carrying text, plus the thread's own identity.

    Replies live in `children[].children` and are not job posts. The story's
    `num_comments` counts replies too, so it is never the post count.

    The raw response is snapshotted BEFORE parsing, so the database can never
    contain rows whose source response was not preserved.

    The item response carries its own `id`, `title` and `created_at` - the
    same fields `find_current_thread` gets from the search hit - so a
    `--thread` backfill can learn the thread's real month without a second
    HTTP request. A missing `created_at` raises rather than falling back to
    today: a wrong posted_at date is worse than a loud failure, because
    `stale_postings` keys on it.
    """
    url = ITEM_URL.format(thread_id=thread_id)
    payload = fetch(url)

    if snapshot_dir is not None:
        snapshot_dir = Path(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        target = snapshot_dir / f"{thread_id}-{date.today().isoformat()}.json"
        target.write_text(json.dumps(payload), encoding="utf-8")

    created_at = payload.get("created_at")
    if not created_at:
        raise ValueError(
            f"thread {thread_id} item response has no created_at - "
            f"cannot determine the thread's month")
    ref = HnThreadRef(id=str(payload.get("id", thread_id)),
                      title=payload.get("title") or "",
                      created_at=created_at)

    children = payload.get("children") or []
    posts = [HnRawPost(item_id=str(c["id"]), text=c["text"],
                       created_at=c.get("created_at"))
             for c in children if c.get("text")]
    if children and not posts:
        raise ValueError(
            f"thread {thread_id} has {len(children)} children but no job posts - "
            f"HN markup may have changed")
    return ref, posts


def fetch_thread(thread_id: str, fetch: Fetcher = httpx_fetcher,
                 snapshot_dir: Path | None = None) -> list[HnRawPost]:
    """Top-level comments carrying text. Those are the job posts.

    Thin delegate over `fetch_thread_with_meta`, kept for callers that only
    need the posts (Task 4's existing tests included).
    """
    _ref, posts = fetch_thread_with_meta(thread_id, fetch, snapshot_dir=snapshot_dir)
    return posts
