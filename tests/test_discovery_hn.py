import json
from pathlib import Path

import pytest

from jobhunt.discovery import hn
from jobhunt.discovery.hn import (ITEM_URL, SEARCH_URL, _TIMEOUT, fetch_thread,
                                  fetch_thread_with_meta, find_current_thread,
                                  httpx_fetcher)

FIX = Path(__file__).parent / "fixtures" / "discovery"
SEARCH = json.loads((FIX / "hn_story_search.json").read_text(encoding="utf-8"))
THREAD = json.loads((FIX / "hn_thread_49522897.json").read_text(encoding="utf-8"))


def make_fetcher(search=SEARCH, thread=THREAD, calls=None):
    def fetch(url: str) -> dict:
        if calls is not None:
            calls.append(url)
        return search if "search" in url else thread
    return fetch


def test_picks_the_hiring_thread_not_the_wants_to_be_hired_sibling():
    ref = find_current_thread(make_fetcher())
    assert "Who is hiring" in ref.title
    assert "wants to be hired" not in ref.title.lower()


def test_picks_the_hiring_thread_even_when_the_sibling_sorts_first():
    """The committed fixture is vacuous for this rule: both stories share a
    created_at, and the hiring story happens to be listed first, so `max()`
    tie-breaks to the right answer even with the title filter removed.

    Build a synthetic payload where the "wants to be hired" story sorts
    strictly first by construction - listed first AND with a strictly newer
    created_at - so only the title filter can produce the right answer.
    """
    synthetic = {"hits": [
        {"objectID": "999", "title": "Ask HN: Who wants to be hired? (September 2026)",
         "created_at": "2026-09-01T15:05:00Z", "author": "whoishiring"},
        {"objectID": "49522897", "title": "Ask HN: Who is hiring? (September 2026)",
         "created_at": "2026-09-01T15:01:17Z", "author": "whoishiring"},
    ]}
    ref = find_current_thread(make_fetcher(search=synthetic))
    assert ref.id == "49522897"
    assert "Who is hiring" in ref.title


def test_picks_the_newest_hiring_thread():
    ref = find_current_thread(make_fetcher())
    hiring = [h for h in SEARCH["hits"] if "Who is hiring" in (h.get("title") or "")]
    newest = max(hiring, key=lambda h: h["created_at"])
    assert ref.id == newest["objectID"]


def test_raises_when_no_hiring_story_is_present():
    empty = {"hits": [{"objectID": "1", "title": "Ask HN: Who wants to be hired? (September 2026)",
                       "created_at": "2026-09-01T15:00:00Z", "author": "whoishiring"}]}
    with pytest.raises(LookupError, match="Who is hiring"):
        find_current_thread(make_fetcher(search=empty))


def test_search_url_uses_https_and_the_author_tag():
    # http://hn.algolia.com does not return JSON, and title matching is fragile.
    assert SEARCH_URL.startswith("https://")
    assert "author_whoishiring" in SEARCH_URL
    assert "search_by_date" in SEARCH_URL


def test_returns_only_top_level_posts_with_text():
    posts = fetch_thread("49522897", make_fetcher())
    assert len(posts) == 260


def test_nested_replies_are_not_treated_as_job_posts():
    nested = sum(len(c.get("children") or []) for c in THREAD["children"])
    assert nested == 82, "fixture must retain nested replies for this test to mean anything"
    posts = fetch_thread("49522897", make_fetcher())
    reply_ids = {str(r["id"]) for c in THREAD["children"] for r in (c.get("children") or [])}
    assert not ({p.item_id for p in posts} & reply_ids)


def test_num_comments_is_not_used_as_the_post_count():
    # num_comments is a SEARCH-hit field; the item response has no such key.
    # It counts replies too and drifts upward over time, so assert the
    # invariant rather than a literal value.
    assert THREAD.get("num_comments") is None
    hit = next(h for h in SEARCH["hits"] if h["objectID"] == "49522897")
    assert hit["num_comments"] > 260
    assert len(fetch_thread("49522897", make_fetcher())) == 260


def test_children_without_text_are_skipped():
    thread = {"id": 9, "title": "Ask HN: Who is hiring? (September 2026)",
             "created_at": "2026-09-01T15:01:17.000Z",
             "children": [{"id": 1, "text": "Acme | Remote US"},
                         {"id": 2, "text": None},
                         {"id": 3}]}
    posts = fetch_thread("9", make_fetcher(thread=thread))
    assert [p.item_id for p in posts] == ["1"]


def test_raises_when_children_exist_but_none_have_text():
    thread = {"id": 9, "title": "Ask HN: Who is hiring? (September 2026)",
             "created_at": "2026-09-01T15:01:17.000Z",
             "children": [{"id": 1, "text": None}, {"id": 2}]}
    with pytest.raises(ValueError, match="no job posts"):
        fetch_thread("9", make_fetcher(thread=thread))


def test_snapshot_is_written_before_parsing(tmp_path):
    fetch_thread("49522897", make_fetcher(), snapshot_dir=tmp_path)
    written = list(tmp_path.glob("49522897-*.json"))
    assert len(written) == 1
    # Assert stable fields: the snapshot must be the full response, with the
    # nested replies surviving into it.
    snapped = json.loads(written[0].read_text(encoding="utf-8"))
    assert len(snapped["children"]) == 260
    assert sum(len(c.get("children") or []) for c in snapped["children"]) == 82


def test_snapshot_failure_raises_before_returning_posts(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OSError):
        fetch_thread("49522897", make_fetcher(), snapshot_dir=blocker)


def test_item_url_is_formatted_with_the_thread_id():
    calls = []
    fetch_thread("49522897", make_fetcher(calls=calls))
    assert any("49522897" in c for c in calls)
    assert ITEM_URL.format(thread_id="49522897").startswith("https://")


def test_fetch_thread_with_meta_returns_the_items_own_ref():
    """A --thread backfill has no search hit to learn the month from - the
    item response carries its own id/title/created_at, so fetch_thread_with_meta
    must build the HnThreadRef from those rather than from the search step."""
    ref, posts = fetch_thread_with_meta("49522897", make_fetcher())
    assert ref.id == "49522897"
    assert ref.created_at == THREAD["created_at"]
    assert len(posts) == 260


def test_fetch_thread_with_meta_raises_when_the_item_has_no_created_at():
    thread = {"id": "9", "title": "Ask HN: Who is hiring? (September 2026)",
             "children": [{"id": 1, "text": "Acme | Remote US"}]}
    with pytest.raises(ValueError, match="9"):
        fetch_thread_with_meta("9", make_fetcher(thread=thread))


class _NonJsonResponse:
    """A response that looks fine at the HTTP level but is not JSON - what
    `http://hn.algolia.com` returns, and what a proxy error page returns."""

    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        raise json.JSONDecodeError("Expecting value", "<html>nope</html>", 0)


class _JsonResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_fetcher_names_the_url_and_the_status_on_a_non_json_response(monkeypatch):
    """Spec section 4 requires both. A bare `.json()` raised a
    JSONDecodeError naming neither, and that string is what lands in
    runs.error - the only record of why a run failed."""
    url = "https://hn.algolia.com/api/v1/items/49522897"
    monkeypatch.setattr(hn.httpx, "get", lambda u, **kw: _NonJsonResponse())
    with pytest.raises(ValueError) as exc:
        httpx_fetcher(url)
    message = str(exc.value)
    assert url in message
    assert "200" in message
    assert "not JSON" in message


def test_fetcher_sets_an_explicit_positive_timeout(monkeypatch):
    """There must be no retry-forever path. `_TIMEOUT = None` - which is
    httpx's "wait indefinitely" - passed every test in the suite."""
    assert isinstance(_TIMEOUT, float)
    assert _TIMEOUT > 0

    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return _JsonResponse({"hits": []})

    monkeypatch.setattr(hn.httpx, "get", fake_get)
    assert httpx_fetcher("https://example.com/x") == {"hits": []}
    assert seen["timeout"] == _TIMEOUT
    assert isinstance(seen["timeout"], float) and seen["timeout"] > 0
