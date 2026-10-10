"""Fetch one company's job board. The ONLY network code in this source.

The fetcher is injected exactly as `hn.py` does it, so no test makes a live
call and every provider shape is exercised against a committed fixture.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable

import httpx

from .ats_parse import parse
from .models import AtsRole

Fetcher = Callable[[str], object]

#: All three are public, documented and unauthenticated. No scraping, so the
#: project's no-Indeed/no-LinkedIn constraint is untouched.
BOARD_URL: dict[str, str] = {
    # content=true is REQUIRED: without it the response carries titles and no
    # body, and the Hybrid Test cannot read a title.
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    # details=true is REQUIRED for the same reason as Greenhouse content=true.
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true",
    "teamtailor": "https://{token}.teamtailor.com/jobs.json",
}


class BoardError(RuntimeError):
    """One company's board could not be read. Never fails the whole run: a
    token extracted from prose can simply be wrong - `jobs.lever.co/checkly`
    404s because Checkly is on Ashby."""


def httpx_fetcher(url: str):
    response = httpx.get(url, timeout=20.0,
                         headers={"User-Agent": "jobhunt/0.1"})
    response.raise_for_status()
    return response.json()


def fetch_board(provider: str, token: str, company: str, *,
                fetch: Fetcher = httpx_fetcher,
                snapshot_dir: Path | None = None) -> list[AtsRole]:
    if provider not in BOARD_URL:
        raise BoardError(f"{company}: unknown provider {provider!r}")
    url = BOARD_URL[provider].format(token=token)
    try:
        payload = fetch(url)
    except Exception as exc:                      # noqa: BLE001
        raise BoardError(f"{company} ({provider}/{token}): {exc}") from exc
    if not isinstance(payload, (dict, list)):
        raise BoardError(
            f"{company} ({provider}/{token}): expected JSON, got "
            f"{type(payload).__name__}")
    if snapshot_dir is not None:
        snapshot_dir = Path(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / f"{provider}-{token}-{date.today().isoformat()}.json"
         ).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    try:
        return parse(provider, token, company, payload)
    except Exception as exc:                      # noqa: BLE001
        raise BoardError(
            f"{company} ({provider}/{token}): parse failed - {exc}") from exc
