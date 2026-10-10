"""Spending money from a browser. The guards matter more than the feature."""

import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from conftest import seeded   # reuse the seeded fixture
from jobhunt.dashboard.actions import (application_dir, application_state,
                                       safe_application_path)
from jobhunt.dashboard.app import create_app
from jobhunt.discovery.store import Store

#: Generating for real needs the real profile - these two tests exercise
#: actual generate.cli code (with a FakeClient standing in for Anthropic),
#: not a mock of run_generation, so they need `data/profile.json` the way
#: `tests/test_generate_cli.py` does.
_PROFILE = Path("tests/fixtures/profile_golden.json")
needs_profile = pytest.mark.skipif(
    not _PROFILE.is_file(), reason="run `python -m jobhunt.ingest` first")


def test_the_directory_name_matches_what_the_cli_writes():
    """Otherwise the dashboard generates a second copy under a different
    name and the 'already exists' guard never fires."""
    from jobhunt.generation.render import slug
    d = application_dir("Runway", "Design Engineer")
    assert d.name == f"{slug('Runway')}-{slug('Design Engineer')}"


def test_state_is_absent_then_ready(tmp_path):
    d = tmp_path / "runway-design-engineer"
    assert application_state(d) == "absent"
    d.mkdir()
    (d / "resume.html").write_text("<html></html>", encoding="utf-8")
    (d / "cover-letter.html").write_text("<html></html>", encoding="utf-8")
    assert application_state(d) == "ready"


def test_a_partial_directory_is_not_ready(tmp_path):
    """A role must stay retryable after a crash. CRITICAL-2 (review round 1)
    rejected a directory-as-lock design precisely because it has a wedge
    mode: a crash after `mkdir` but before both documents land would leave a
    directory that reads as "in progress" forever. The real guard is an
    in-process lock (`actions.lock_for`), so a directory with some but not
    all of the required files must never read as finished - it has no
    bearing on whether a generation is in flight."""
    d = tmp_path / "x"; d.mkdir()
    assert application_state(d) != "ready"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "..", "a/../../b"])
def test_path_traversal_is_refused(tmp_path, bad):
    """REVIEW FOCUS 5."""
    with pytest.raises(ValueError):
        safe_application_path(tmp_path, bad, "resume.html")


def test_only_known_filenames_are_served(tmp_path):
    with pytest.raises(ValueError):
        safe_application_path(tmp_path, "runway-design-engineer", "secrets.env")


def test_generate_is_not_reachable_by_get(tmp_path, monkeypatch):
    """REVIEW FOCUS 1. A GET that spends can be fired by a prefetcher.

    Stubs run_generation and APPLICATIONS_ROOT so that if the POST-only
    guard ever regresses, this test fails on an assertion instead of
    silently placing a real, billed call against the live Anthropic API."""
    import jobhunt.dashboard.actions as A

    def must_not_be_called(*a, **k):
        raise AssertionError("a GET must never reach run_generation")
    monkeypatch.setattr(A, "run_generation", must_not_be_called)
    monkeypatch.setattr(A, "APPLICATIONS_ROOT", tmp_path / "apps")

    c = TestClient(create_app(seeded(tmp_path)))
    r = c.get("/generate/ashby:runway:1")
    assert r.status_code in (404, 405)


def test_a_ready_application_is_not_regenerated(tmp_path, monkeypatch):
    """REVIEW FOCUS 2. A second click must open, never spend again."""
    import jobhunt.dashboard.actions as A
    db = seeded(tmp_path)
    out = tmp_path / "apps" / "runway-design-engineer"
    out.mkdir(parents=True)
    for f in ("resume.html", "cover-letter.html"):
        (out / f).write_text("<html></html>", encoding="utf-8")
    calls = []
    monkeypatch.setattr(A, "run_generation",
                        lambda *a, **k: calls.append(1))
    monkeypatch.setattr(A, "APPLICATIONS_ROOT", tmp_path / "apps")

    c = TestClient(create_app(db))
    r = c.post("/generate/ashby:runway:1")

    assert r.status_code == 200
    assert calls == [], "a ready application must not be regenerated"
    assert "resume.html" in r.text


def test_a_failed_generation_reports_the_reason(tmp_path, monkeypatch):
    """REVIEW FOCUS 3. Never revert silently - that reads as a dead click."""
    import jobhunt.dashboard.actions as A
    db = seeded(tmp_path)
    monkeypatch.setattr(A, "APPLICATIONS_ROOT", tmp_path / "apps")

    def boom(*a, **k):
        raise RuntimeError("credit balance is too low")
    monkeypatch.setattr(A, "run_generation", boom)

    r = TestClient(create_app(db)).post("/generate/ashby:runway:1")

    assert r.status_code == 200          # an HTMX fragment, not a 500 page
    assert "credit balance is too low" in r.text


def test_the_card_shows_the_price_before_generating(tmp_path, monkeypatch):
    """`"10" in body` alone also matches every role's "x/10" score - this
    must check the actual price text, or removing it from the button would
    leave the assertion passing."""
    import jobhunt.dashboard.actions as A
    monkeypatch.setattr(A, "APPLICATIONS_ROOT", tmp_path / "apps")
    body = TestClient(create_app(seeded(tmp_path))).get("/").text
    assert "~10¢" in body and "Generate" in body


def test_the_apply_link_is_present(tmp_path, monkeypatch):
    import jobhunt.dashboard.actions as A
    monkeypatch.setattr(A, "APPLICATIONS_ROOT", tmp_path / "apps")
    assert "https://apply/here" in TestClient(create_app(seeded(tmp_path))).get("/").text


def test_two_concurrent_posts_enter_run_generation_once(tmp_path, monkeypatch):
    """CRITICAL-2 (review round 1). The CLI's own `existing` check is no
    backstop: the first request only writes its files AFTER the (stubbed,
    here instant-but-slow) API calls finish, so a second request arriving
    while the first is in flight would pass that check too and both would
    pay. The guard must be an in-process lock entered before either request
    calls run_generation."""
    import jobhunt.dashboard.actions as A
    db = seeded(tmp_path)
    monkeypatch.setattr(A, "APPLICATIONS_ROOT", tmp_path / "apps")

    calls = []
    entered = threading.Event()
    release = threading.Event()

    def slow_run(*a, **k):
        calls.append(1)
        entered.set()
        release.wait(timeout=2)

    monkeypatch.setattr(A, "run_generation", slow_run)

    c = TestClient(create_app(db))
    results = []

    def post():
        results.append(c.post("/generate/ashby:runway:1"))

    t1 = threading.Thread(target=post)
    t1.start()
    assert entered.wait(timeout=2), "first request never reached run_generation"

    t2 = threading.Thread(target=post)
    t2.start()
    t2.join(timeout=2)
    release.set()
    t1.join(timeout=2)

    assert len(calls) == 1, "a concurrent second click must not call run_generation again"
    assert len(results) == 2
    assert all(r.status_code == 200 for r in results)


@needs_profile
def test_run_generation_also_renders_the_documents_the_dashboard_opens(tmp_path):
    """CRITICAL-1 (review round 1). generate.cli.main, called with a posting
    id, writes only resume.md/cover-letter.md/posting.md. resume.html and
    cover-letter.html - the exact files `application_state` checks for -
    are written by the separate `--render DIR` pass, which the original
    run_generation never invoked. That made the feature non-functional: the
    route reported "ready", both document links 404'd, and a retry hit the
    CLI's own `existing and not --force` guard forever. Uses the generate
    suite's FakeClient so no real API call is made."""
    import jobhunt.dashboard.actions as A
    from test_generate_cli import FakeClient, VERDICT
    from test_generate_cli import posting as gen_posting

    db = tmp_path / "j.sqlite"
    s = Store(db)
    s.init_schema()
    s.upsert_posting(gen_posting("ashby:runway-ml:1", "Runway"))
    s.save_role_verdict("ashby:runway-ml:1", VERDICT, model="m", prompt_sha="s")
    s.close()

    out_dir = tmp_path / "apps" / "runway-design-engineer"
    A.run_generation(db, "ashby:runway-ml:1", out_dir, client=FakeClient())

    assert (out_dir / "resume.html").is_file()
    assert (out_dir / "cover-letter.html").is_file()
    assert A.application_state(out_dir) == "ready"


@needs_profile
def test_run_generation_surfaces_the_clis_real_failure_reason(tmp_path):
    """IMPORTANT-3 (review round 1). generate.cli.main prints the actual
    reason ("no role verdict", "already exists with N files", "no eligible
    bullets") to stderr and exits 1; the original run_generation discarded
    it and always raised the same generic "see the server log" message, so
    every failure mode rendered identically. This posting has no stored
    verdict, which `_generate` reports by name - no API call is reachable
    on this path, so no client is needed."""
    import jobhunt.dashboard.actions as A
    from test_generate_cli import posting as gen_posting

    db = tmp_path / "j.sqlite"
    s = Store(db)
    s.init_schema()
    s.upsert_posting(gen_posting("ashby:runway-ml:1", "Runway"))  # no verdict
    s.close()

    out_dir = tmp_path / "apps" / "runway-design-engineer"
    with pytest.raises(RuntimeError, match="role verdict"):
        A.run_generation(db, "ashby:runway-ml:1", out_dir)


# --- a failure must say WHY ------------------------------------------------
# 2026-10-07: a generation failed and the dashboard showed
#   "failed: resume selection: API error - Error code: 400 - {'type': 'er"
# which is exactly `error[:60]`. The reason the API gave was cut off one
# character before it began, so the user could not act on it. The full text
# was only in a hover `title` attribute.

from jobhunt.dashboard.actions import friendly_error


def test_the_api_message_is_extracted_from_the_raw_error():
    raw = ("resume selection: API error - Error code: 400 - {'type': 'error', "
           "'error': {'type': 'invalid_request_error', 'message': 'Your credit "
           "balance is too low to access the Anthropic API.'}}")
    assert friendly_error(raw) == (
        "resume selection: Your credit balance is too low to access the "
        "Anthropic API.")


def test_a_double_quoted_message_is_also_extracted():
    raw = 'cover letter: API error - Error code: 429 - {"error": {"message": "rate limit"}}'
    assert friendly_error(raw) == "cover letter: rate limit"


def test_an_error_with_no_api_message_is_passed_through():
    assert friendly_error("no role verdict for posting x") == (
        "no role verdict for posting x")


def test_the_stage_label_is_kept_so_he_knows_which_call_failed():
    raw = ("cover letter: API error - Error code: 400 - {'error': "
           "{'message': 'prompt is too long'}}")
    assert friendly_error(raw).startswith("cover letter:")


def test_a_very_long_message_is_still_returned_whole():
    """Truncation belongs in the template, not here - and the template must
    show enough to be actionable."""
    raw = "x: API error - Error code: 400 - {'error': {'message': '" + "y" * 400 + "'}}"
    assert len(friendly_error(raw)) > 300


def test_none_and_empty_are_safe():
    assert friendly_error("") == ""
    assert friendly_error(None) == ""


# --- the wedge: a directory with markdown but no rendered HTML -------------

class _ExplodingClient:
    """Any API call is a test failure here - completing a render must cost
    nothing. Nine real applications were left in this state on 2026-10-07."""
    @property
    def messages(self):
        raise AssertionError("run_generation called the API for a render-only "
                             "recovery")


def test_an_unrendered_directory_is_completed_without_an_api_call(tmp_path):
    """`run_generation` called the generate pass unconditionally, so a
    directory holding only markdown made the CLI refuse with 'already exists'
    and the render pass never ran. The state was then unreachable by any
    recovery available from the browser."""
    from jobhunt.dashboard.actions import application_state, run_generation
    out = tmp_path / "apps"
    d = out / "acme-designer"
    d.mkdir(parents=True)
    (d / "resume.md").write_text("# Sam Example\n\n## Summary\n\nx\n",
                                 encoding="utf-8")
    (d / "cover-letter.md").write_text("Dear Team,\n\nx\n", encoding="utf-8")
    (d / "posting.md").write_text(
        "# Designer - Acme\n\n- company: Acme\n- role: Designer\n",
        encoding="utf-8")
    assert application_state(d) != "ready"

    run_generation(tmp_path / "nonexistent.sqlite", "a:1", d,
                   client=_ExplodingClient())

    assert application_state(d) == "ready"
    assert (d / "resume.html").is_file()
