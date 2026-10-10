import json
from pathlib import Path

import pytest

from jobhunt.discovery.hn_parse import (clean_post_text, company_of,
                                        first_url_of, geo_signal_of, is_remote,
                                        parse_post)
from jobhunt.discovery.models import HnRawPost

FIXTURE = Path(__file__).parent / "fixtures" / "discovery" / "hn_thread_49522897.json"


@pytest.fixture(scope="module")
def raw_posts():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [HnRawPost(item_id=str(c["id"]), text=c["text"], created_at=c.get("created_at"))
            for c in data["children"] if c.get("text")]


def test_fixture_has_the_measured_post_count(raw_posts):
    assert len(raw_posts) == 260


def test_double_unescape_is_applied():
    """The second unescape pass, exercised with a DOUBLE-escaped URL.

    This input is synthetic - it is the shape the Algolia SEARCH endpoint's
    `comment_text` returns. The /items/<id> endpoint this slice actually uses
    is single-escaped (see the test below), so the second pass is defensive
    insurance rather than a requirement.
    """
    raw = "Acme | Engineer | Remote US | https:&amp;#x2F;&amp;#x2F;acme.com"
    cleaned = clean_post_text(raw)
    assert "&#x2F;" not in cleaned
    assert "https://acme.com" in cleaned


def test_the_item_endpoint_is_single_escaped(raw_posts):
    """Corrects spec section 3.2. 254 of the 260 real posts contain `&#x2F;`
    and ZERO contain `&amp;#x2F;`, so one pass already suffices here."""
    singly = [r for r in raw_posts if "&#x2F;" in r.text]
    doubly = [r for r in raw_posts if "&amp;#x2F;" in r.text]
    assert len(singly) == 254
    assert doubly == []


def test_one_unescape_pass_already_clears_the_item_endpoint(raw_posts):
    import html
    assert not any("&#x2F;" in html.unescape(r.text) for r in raw_posts)


def test_the_second_unescape_pass_is_not_idempotent_on_quoted_markup():
    """The documented cost of keeping the second pass: an author quoting
    escaped markup loses it. `&amp;lt;div&amp;gt;` becomes `&lt;div&gt;` after
    one pass and `<div>` after two, and the tag stripper then deletes it.
    Zero occurrences in the September thread.
    """
    cleaned = clean_post_text("Acme | Engineer | use &amp;lt;div&amp;gt; in the template")
    assert "div" not in cleaned
    assert cleaned == "Acme | Engineer | use in the template"


def test_clean_strips_tags_but_keeps_paragraph_breaks():
    cleaned = clean_post_text("<p>Acme | Engineer<p>We build things.")
    assert "<p>" not in cleaned and "<" not in cleaned
    assert "\n" in cleaned
    assert cleaned.split("\n")[0] == "Acme | Engineer"


def test_clean_collapses_spaces_but_not_newlines():
    cleaned = clean_post_text("Acme    |     Engineer<p>line    two")
    assert cleaned.startswith("Acme | Engineer")
    assert "line two" in cleaned


def test_company_from_a_piped_first_line():
    assert company_of("Modash.io | Senior Product Engineer | Remote (Europe)") == "Modash.io"


def test_company_strips_an_inline_url():
    assert company_of("Snout https://snout.com/ | Multiple Roles | Remote US") == "Snout"


def test_company_strips_a_parenthesised_url():
    assert company_of("Smarkets (https://www.smarkets.com) | Full Time | London") == "Smarkets"


def test_company_keeps_a_bracket_the_pipe_split_orphaned():
    """`.strip("()[]")` removed the closing paren the pipe split left behind,
    turning "Monumint (YC W24) | ..." into "Monumint (YC W24" - an
    unbalanced bracket on 15 of the 260 real companies. Display damage only
    (the normalised form is unaffected), but wrong on a field slice 5 shows.
    """
    assert company_of("Monumint (YC W24) | Senior Engineer | Remote US") == "Monumint (YC W24)"
    assert company_of("Shepherd (Series B) | Backend | Remote US") == "Shepherd (Series B)"


def test_company_still_strips_a_bracket_that_leaves_the_rest_balanced():
    # A stray bracket with no partner is still noise, and removing it does
    # not create an imbalance.
    assert company_of("Acme] | Engineer") == "Acme"
    assert company_of("(Acme | Engineer") == "Acme"


def test_company_leaves_an_orphaned_opening_bracket_rather_than_unbalancing():
    assert company_of("Acme (YC W24 | Engineer") == "Acme (YC W24"


def test_no_real_company_has_an_unbalanced_bracket(raw_posts):
    for raw in raw_posts:
        c = parse_post(raw).company
        assert c.count("(") == c.count(")"), c
        assert c.count("[") == c.count("]"), c


def test_company_falls_back_to_a_truncated_first_line_when_there_are_no_pipes():
    line = ("Rust developer/software engineer at NLnet foundation in Amsterdam "
            "doing important work")
    got = company_of(line)
    assert got.startswith("Rust developer/software engineer")
    assert len(got) <= 60


def test_every_real_post_yields_a_non_empty_company(raw_posts):
    companies = [parse_post(p).company for p in raw_posts]
    assert len(companies) == 260
    assert all(c.strip() for c in companies)
    assert not any("<" in c or "&#x" in c for c in companies)


def test_known_companies_are_extracted_exactly(raw_posts):
    companies = {parse_post(p).company for p in raw_posts}
    for expected in ("Modash.io", "Quill", "Snout", "Smarkets", "Cerity Partners"):
        assert expected in companies


def test_first_url_found_after_unescaping():
    cleaned = clean_post_text("Acme | Remote US | https:&amp;#x2F;&amp;#x2F;acme.com/jobs")
    assert first_url_of(cleaned) == "https://acme.com/jobs"


def test_first_url_is_none_when_absent():
    assert first_url_of("Acme | Engineer | Remote US") is None


def test_is_remote_detection():
    assert is_remote("Acme | Engineer | REMOTE")
    assert is_remote("Acme | fully remote role")
    assert not is_remote("Acme | Engineer | Onsite NYC")


def test_geo_signal_classification():
    assert geo_signal_of("Remote US only, ET hours") == "us"
    assert geo_signal_of("Remote (Europe), CET") == "eu_only"
    assert geo_signal_of("Remote worldwide") == "us"      # worldwide includes the US
    assert geo_signal_of("Remote") == "unknown"


def test_the_english_pronoun_us_is_not_a_us_marker():
    """The abbreviation pattern is case-SENSITIVE for exactly this reason.
    With re.I, \\bUS\\b matched "email us" and \\bET\\b matched "et al",
    mislabelling 39 of 260 posts and passing four EU-only roles as candidates.
    """
    assert geo_signal_of("Remote (EU). Please email us to apply.") == "eu_only"
    assert geo_signal_of("Remote (Europe) - come join us!") == "eu_only"
    assert geo_signal_of("A lot of us work remotely.") == "unknown"
    assert geo_signal_of("Remote (Europe), see Smith et al for our research.") == "eu_only"


def test_uppercase_abbreviations_are_still_us_markers():
    assert geo_signal_of("Remote US") == "us"
    assert geo_signal_of("Remote, U.S. only") == "us"
    assert geo_signal_of("Remote USA") == "us"
    assert geo_signal_of("Remote, ET timezone") == "us"
    assert geo_signal_of("Remote, PST hours") == "us"


def test_word_form_us_markers_stay_case_insensitive():
    assert geo_signal_of("remote from anywhere") == "us"
    assert geo_signal_of("Remote WORLDWIDE") == "us"
    assert geo_signal_of("Hiring across the united states") == "us"


def test_us_marker_beats_an_eu_marker():
    # A post saying "EMEA or US" is not EU-only.
    assert geo_signal_of("Remote EMEA or US") == "us"


def test_parse_post_id_is_prefixed_and_stable():
    p = parse_post(HnRawPost(item_id="49522903", text="Acme | Remote US"))
    assert p.id == "hn:49522903"


def test_parse_post_raises_on_empty_text():
    with pytest.raises(ValueError, match="empty"):
        parse_post(HnRawPost(item_id="1", text="   <p>  "))
