"""The routes. Structure only - styling is DESIGN.md's job, not a test's."""

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from conftest import seeded
from jobhunt.dashboard.app import create_app

CSS = Path("src/jobhunt/dashboard/static/dashboard.css")
DESIGN = Path("DESIGN.md")


def client(tmp_path):
    return TestClient(create_app(seeded(tmp_path)))


def test_the_page_renders(tmp_path):
    r = client(tmp_path).get("/")
    assert r.status_code == 200
    assert "Runway" in r.text and "Design Engineer" in r.text


def test_the_score_and_the_reason_are_both_on_the_page(tmp_path):
    body = client(tmp_path).get("/").text
    assert "/12" in body
    assert "Spans design and engineering." in body


def test_the_apply_url_is_linked(tmp_path):
    assert "https://apply/here" in client(tmp_path).get("/").text


def test_the_header_partial_serves_on_its_own_for_htmx(tmp_path):
    r = client(tmp_path).get("/partials/header")
    assert r.status_code == 200
    assert "<html" not in r.text.casefold()


def test_the_dashboard_never_writes(tmp_path):
    """Read-only by construction: the Store is opened mode=ro."""
    db = seeded(tmp_path)
    before = db.stat().st_mtime_ns
    TestClient(create_app(db)).get("/")
    assert db.stat().st_mtime_ns == before


def test_a_missing_database_fails_at_startup_not_as_an_empty_page(tmp_path):
    with pytest.raises(Exception):
        create_app(tmp_path / "absent.sqlite")


def test_an_out_of_date_database_refuses_to_start(tmp_path):
    """REVIEW FOCUS 3. A read-only dashboard must never migrate a database out
    from under a running CLI - it must say which command to run instead."""
    import sqlite3
    db = seeded(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="init_schema|migrat|out of date"):
        create_app(db)


def test_the_stylesheet_defines_every_design_token(tmp_path):
    """REVIEW FOCUS 5. Otherwise DESIGN.md and the CSS drift silently."""
    css = CSS.read_text(encoding="utf-8")
    for token in ("--ink", "--ink-2", "--ink-3", "--rule", "--paper",
                  "--muted", "--signal", "--patina", "--ff-display",
                  "--ff-body", "--ff-mono", "--shell"):
        assert token in css, f"{token} from DESIGN.md is missing from the CSS"


def test_the_stylesheet_introduces_no_forbidden_colour():
    """DESIGN.md: no red, no green, no severity palette. Checked as a rule,
    because it is the prohibition an agent is most likely to violate."""
    css = CSS.read_text(encoding="utf-8").casefold()
    for banned in ("red", "green", "#f00", "#0f0", "crimson", "tomato",
                   "limegreen", "seagreen"):
        assert banned not in css, f"DESIGN.md forbids {banned!r}"


def test_the_page_declares_the_dark_theme(tmp_path):
    assert 'data-bs-theme="dark"' in client(tmp_path).get("/").text


def test_an_action_filter_narrows_the_page(tmp_path):
    c = client(tmp_path)
    full = c.get("/").text
    filtered = c.get("/?action=drop").text
    assert "Design Engineer" in full
    assert "Design Engineer" not in filtered


def test_the_filtered_bucket_still_renders(tmp_path):
    body = client(tmp_path).get("/?action=apply_priority").text
    assert "Design Engineer" in body


def test_an_unknown_action_shows_everything(tmp_path):
    """REVIEW FOCUS 4. A typo'd or stale URL must not look like no results."""
    body = client(tmp_path).get("/?action=not_a_bucket").text
    assert "Design Engineer" in body


def test_no_action_shows_everything(tmp_path):
    assert "Design Engineer" in client(tmp_path).get("/").text


def test_the_chips_link_to_their_filter(tmp_path):
    body = client(tmp_path).get("/").text
    assert "?action=apply_priority" in body
    assert 'href="/"' in body          # the "all" chip clears it


def test_the_active_chip_is_marked(tmp_path):
    body = client(tmp_path).get("/?action=apply_priority").text
    assert "chip--on" in body


def test_the_header_partial_preserves_the_active_filter(tmp_path):
    """IMPORTANT-4 (review round 1). Without `action` on `/partials/header`,
    `active` was Undefined - falsy in Jinja - so the 10-second poll always
    re-rendered with "all" highlighted while the page body still showed only
    the filtered bucket. The partial must resolve `active` exactly like `/`
    does, and propagate it in its own hx-get so the NEXT poll does too.

    Checks the SPECIFIC chips' classes, not just that "chip--on" occurs
    somewhere - the "all" chip gets it whenever `active` is falsy, which is
    exactly the bug: a loose "chip--on" in body" assertion would have
    passed before this was fixed."""
    r = client(tmp_path).get("/partials/header?action=apply_priority")
    assert r.status_code == 200
    assert "/partials/header?action=apply_priority" in r.text

    all_chip = re.search(r'<a class="([^"]*)"\s+href="/"[\s>]', r.text)
    active_chip = re.search(
        r'<a class="([^"]*)"\s+href="/\?action=apply_priority"[\s>]', r.text)
    assert all_chip and "chip--on" not in all_chip.group(1)
    assert active_chip and "chip--on" in active_chip.group(1)


# ---- the UX filter -----------------------------------------------------

def ux_client(tmp_path):
    """`seeded` plus one role whose description mentions UX."""
    from jobhunt.discovery.models import Posting
    from jobhunt.discovery.store import Store
    from jobhunt.matching.models import RoleVerdict
    db = seeded(tmp_path)
    s = Store(db)
    p = Posting(id="ashby:runway:2", source="ashby",
                source_thread_id="runway", company="Runway",
                company_normalised="runway",
                raw_text="Craft the UX of our editor. " + "duties " * 60,
                apply_url="https://apply/ux", is_remote=True,
                geo_signal="us", posted_at="2026-09-02", first_seen="a",
                last_seen="a", status="new")
    s.upsert_posting(p)
    s.save_role_verdict(p.id, RoleVerdict(
        role_title="Product Designer", discipline="design",
        reason="Owns the editor.", hybrid_test="n/a", domain_fit="none",
        hybrid_evidence=[], overlaps=["prototyping"], gaps=[],
        role_fit="strong", location="Remote"), model="m", prompt_sha="s")
    s.close()
    return TestClient(create_app(db))


def test_the_ux_filter_narrows_the_page(tmp_path):
    body = ux_client(tmp_path).get("/?ux=1").text
    assert "Product Designer" in body
    assert "Design Engineer" not in body


def test_the_ux_filter_combines_with_an_action(tmp_path):
    c = ux_client(tmp_path)
    assert "Product Designer" in c.get("/?action=apply_priority&ux=1").text
    assert "Product Designer" not in c.get("/?action=drop&ux=1").text


def test_an_unknown_ux_value_shows_everything(tmp_path):
    body = ux_client(tmp_path).get("/?ux=banana").text
    assert "Design Engineer" in body and "Product Designer" in body


def test_the_ux_chip_toggles_and_keeps_the_action(tmp_path):
    c = ux_client(tmp_path)
    assert "?action=apply_priority&amp;ux=1" in c.get("/?action=apply_priority").text
    on = c.get("/?action=apply_priority&ux=1").text
    assert 'href="/?action=apply_priority"' in on      # click again: off


def test_the_header_partial_preserves_the_ux_filter(tmp_path):
    body = ux_client(tmp_path).get("/partials/header?action=apply_priority&ux=1").text
    assert 'hx-get="/partials/header?action=apply_priority&amp;ux=1"' in body


# ---- the designer filter ----------------------------------------------------

def test_the_designer_filter_narrows_the_page(tmp_path):
    body = ux_client(tmp_path).get("/?designer=1").text
    assert "Product Designer" in body and "Design Engineer" not in body


def test_the_designer_filter_combines_with_ux_and_an_action(tmp_path):
    c = ux_client(tmp_path)
    assert "Product Designer" in c.get("/?action=apply_priority&ux=1&designer=1").text
    assert "Product Designer" not in c.get("/?action=drop&designer=1").text


def test_an_unknown_designer_value_shows_everything(tmp_path):
    body = ux_client(tmp_path).get("/?designer=banana").text
    assert "Design Engineer" in body and "Product Designer" in body


def test_the_designer_chip_toggles_and_keeps_the_other_filters(tmp_path):
    c = ux_client(tmp_path)
    assert "?action=apply_priority&amp;ux=1&amp;designer=1" in c.get("/?action=apply_priority&ux=1").text
    on = c.get("/?action=apply_priority&ux=1&designer=1").text
    assert 'href="/?action=apply_priority&amp;ux=1"' in on


def test_the_header_partial_preserves_the_designer_filter(tmp_path):
    body = ux_client(tmp_path).get("/partials/header?designer=1").text
    assert 'hx-get="/partials/header?designer=1"' in body


# ---- header layout: stats row, filters row, hover tips ---------------------

def _rows_of(html):
    stats = html.split('class="strip__row strip__row--stats"', 1)[1].split("</div>", 1)[0]
    filters = html.split('class="strip__row strip__row--filters"', 1)[1].split("</div>", 1)[0]
    return stats, filters


def test_stats_and_filters_sit_in_separate_rows(tmp_path):
    stats, filters = _rows_of(ux_client(tmp_path).get("/").text)
    assert "scored" in stats and "awaiting" in stats and "prefiltered" in stats
    assert "href" not in stats                       # nothing in row 1 is clickable
    assert "?ux=1" in filters and "?designer=1" in filters and 'href="/"' in filters


def test_every_header_chip_has_a_hover_tip(tmp_path):
    body = ux_client(tmp_path).get("/partials/header").text
    chips = len(re.findall(r'class="chip[ "]', body))     # not chip__k labels
    assert chips and body.count("data-tip=") == chips
