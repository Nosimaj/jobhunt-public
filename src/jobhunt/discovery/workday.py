"""Workday career sites (the unofficial, public CXS API). The ONLY Workday
network code. See CLAUDE.md, "Workday".

Unlike the other five providers a board is not one GET: the list is paged 20
at a time and each description is a separate call, so the HTTP callables are
injected here rather than through `ats.py`'s single-URL fetcher.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import httpx

from ..matching.titles import prefilter_drop
from .ats import BoardError
from .ats_parse import strip_html
from .models import AtsRole

#: `{tenant}.{wdN}/{site}[?locations=id[,id...]]`.
_TOKEN_RE = re.compile(
    r"^(?P<tenant>[a-z0-9_-]+)\.(?P<wd>wd\d+)/(?P<site>[A-Za-z0-9_-]+)"
    r"(?:\?locations=(?P<locs>[A-Za-z0-9]+(?:,[A-Za-z0-9]+)*))?$")


@dataclass(frozen=True)
class WorkdayBoard:
    tenant: str
    wd: str
    site: str
    locations: tuple[str, ...] = ()

    @property
    def base(self) -> str:
        return (f"https://{self.tenant}.{self.wd}.myworkdayjobs.com"
                f"/wday/cxs/{self.tenant}/{self.site}")

    @property
    def list_url(self) -> str:
        return f"{self.base}/jobs"

    def detail_url(self, external_path: str) -> str:
        return f"{self.base}{external_path}"

    def facets(self) -> dict:
        return {"locations": list(self.locations)} if self.locations else {}


def parse_token(token: str) -> WorkdayBoard:
    m = _TOKEN_RE.match(token or "")
    if m is None:
        raise ValueError(
            f"bad Workday token {token!r}: expected "
            "'{tenant}.wdN/{site}' optionally followed by '?locations=id,id'")
    locs = tuple(m["locs"].split(",")) if m["locs"] else ()
    return WorkdayBoard(m["tenant"], m["wd"], m["site"], locs)


#: The API refuses limit > 20 with HTTP 400.
PAGE_SIZE = 20
#: 8,000 rows. Large tenants list a few thousand; past this one is assumed broken.
MAX_PAGES = 400
#: The API never reports a `total` above this, though more rows exist.
TOTAL_CAP = 2000
#: A walk returning under this fraction of `total` is treated as truncated.
MIN_COMPLETENESS = 0.9

Post = Callable[[str, dict], object]
Get = Callable[[str], object]

_HEADERS = {"User-Agent": "jobhunt/0.1", "Accept": "application/json",
            "Content-Type": "application/json"}


def httpx_post(url: str, body: dict):
    r = httpx.post(url, json=body, headers=_HEADERS, timeout=30.0)
    r.raise_for_status()
    return r.json()


def httpx_get(url: str):
    r = httpx.get(url, headers=_HEADERS, timeout=30.0)
    r.raise_for_status()
    return r.json()


def list_jobs(board: WorkdayBoard, post: Post, *, pause: float = 0.0) -> list[dict]:
    """Every listed row. Pages until an empty, short or all-repeat page,
    never until `total`: `total` is only populated on offset 0 and is capped
    (a tenant can report 2000 while offset 2500 still returns rows). Any
    exception propagates - the caller fails the whole board, because a
    partial list must never reach `reconcile_board`."""
    rows: list[dict] = []
    seen: set[str] = set()
    total = 0
    for page in range(MAX_PAGES):
        body = {"appliedFacets": board.facets(), "limit": PAGE_SIZE,
                "offset": page * PAGE_SIZE, "searchText": ""}
        payload = post(board.list_url, body)
        # A 200 carrying an error body has no `jobPostings` key. Reading it
        # as the end of the list would close every row not yet listed.
        if not isinstance(payload, dict) or "jobPostings" not in payload:
            raise RuntimeError(f"{board.tenant}: page {page} has no jobPostings")
        if page == 0:
            total = int(payload.get("total") or 0)
        batch = payload["jobPostings"] or []
        if not batch:
            break
        fresh = 0
        for row in batch:
            # Rows shift while paging when a posting is removed mid-walk.
            key = row.get("externalPath")
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            fresh += 1
        # Some tenants never serve an empty page: past the end they WRAP to
        # page 0 (seen live on a tenant queried with a location facet). So a
        # short page is the last page, and a page of only repeats means the
        # walk has wrapped.
        if len(batch) < PAGE_SIZE or not fresh:
            break
        if pause:
            time.sleep(pause)
    else:
        raise RuntimeError(f"{board.tenant}: no empty page after {MAX_PAGES} pages")
    # `total` is capped at TOTAL_CAP, so it bounds from below only up to there.
    # A walk that ends far short of it ended early, and a short list must
    # never reach `reconcile_board`. The tolerance absorbs the postings that
    # legitimately close during a multi-minute walk.
    expected = min(total, TOTAL_CAP)
    if expected and len(rows) < expected * MIN_COMPLETENESS:
        raise RuntimeError(f"{board.tenant}: list ended short - "
                           f"{len(rows)} of {expected} rows")
    return rows


_REMOTE_RE = re.compile(r"\bremote\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_ONSITE_RE = re.compile(r"\bon[- ]?site\b", re.I)


def role_id(token: str, external_path: str) -> str:
    return f"workday:{token}:{external_path.rstrip('/').rsplit('/', 1)[-1]}"


def _workplace(remote_type: str | None, location: str) -> str:
    """`remoteType` is optional, per-tenant free text ("Onsite Required")."""
    text = remote_type or ""
    if _REMOTE_RE.search(text) or _REMOTE_RE.search(location):
        return "remote"
    if _HYBRID_RE.search(text):
        return "hybrid"
    if _ONSITE_RE.search(text):
        return "onsite"
    return "unknown"


def parse_detail(token: str, company: str, payload, fallback_title: str,
                 external_path: str = "") -> AtsRole | None:
    """One detail response -> one role, or None when it carries no body."""
    info = (payload or {}).get("jobPostingInfo") or {}
    html = info.get("jobDescription")
    if not html:
        return None
    # The list row's `locationsText` can be a summary ("3 Locations"), so the
    # location always comes from here.
    location = info.get("location") or ""
    path = external_path or info.get("externalPath") or info.get("jobReqId") or ""
    return AtsRole(
        id=role_id(token, path or (info.get("title") or fallback_title)),
        provider="workday", token=token, company=company,
        title=info.get("title") or fallback_title,
        body=strip_html(html), location=location,
        workplace_type=_workplace(info.get("remoteType"), location),
        department="", apply_url=info.get("externalUrl"),
        posted_at=(info.get("startDate") or date.today().isoformat())[:10])


@dataclass
class WorkdayResult:
    roles: list[AtsRole] = field(default_factory=list)
    #: EVERY listed id - prefiltered and already-stored included. Closure
    #: reconciles against this, never against `roles`.
    listed_ids: set[str] = field(default_factory=set)
    prefiltered: int = 0
    skipped: int = 0


def fetch_workday(token: str, company: str, *, post: Post, get: Get,
                  known_ids: set[str], pause: float = 0.5) -> WorkdayResult:
    """List every page, then detail-fetch only rows that survive the title
    prefilter AND are not already stored. Raises `BoardError` when the list
    cannot be read in full; a failed detail costs one role, not the board."""
    try:
        board = parse_token(token)
    except ValueError as exc:
        raise BoardError(f"{company} (workday/{token}): {exc}") from exc
    try:
        rows = list_jobs(board, post, pause=pause)
    except Exception as exc:                      # noqa: BLE001
        raise BoardError(f"{company} (workday/{token}): {exc}") from exc

    res = WorkdayResult()
    for row in rows:
        path = row.get("externalPath")
        if not path:
            continue
        rid = role_id(token, path)
        res.listed_ids.add(rid)
        if rid in known_ids:
            continue                     # stored body is kept, not re-fetched
        if prefilter_drop(row.get("title")):
            res.prefiltered += 1         # never stored, so a loosened
            continue                     # titles.py un-drops it next poll
        try:
            role = parse_detail(token, company, get(board.detail_url(path)),
                                row.get("title") or "", path)
        except Exception:                # noqa: BLE001 - one role, not the board
            role = None
        if role is None:
            res.skipped += 1             # unstored, so retried next poll
        else:
            res.roles.append(role)
        if pause:
            time.sleep(pause)
    return res
