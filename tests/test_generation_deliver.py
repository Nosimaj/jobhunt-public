"""Turning the EDITED markdown into deliverables.

Reads what Sam edited, never what was generated - so the output always
matches what he actually sends.
"""

import pytest
import json

from jobhunt.generation.deliver import (application_json, markdown_to_html,
                                        markdown_to_text)

RESUME = """# Sam Example

Designer who builds

Boston, MA · e@example.com

## Summary

Does things.

## Experience

### Senior Designer · Acme
*2020 — 2021*

- Shipped the design system.

## Skills

Design systems, Python
"""


def test_text_output_strips_markdown_syntax():
    txt = markdown_to_text(RESUME)
    assert "# " not in txt and "*2020" not in txt
    assert "Sam Example" in txt and "Shipped the design system." in txt


def test_text_output_keeps_bullets_readable():
    assert "- Shipped the design system." in markdown_to_text(RESUME) or \
           "• Shipped the design system." in markdown_to_text(RESUME)


def test_html_output_is_self_contained_and_printable():
    html = markdown_to_html(RESUME, title="Resume")
    assert "<html" in html and "</html>" in html
    assert "@media print" in html
    assert "<link" not in html and "<script" not in html


def test_html_escapes_content_that_looks_like_markup():
    html = markdown_to_html("# A < B & C", title="t")
    assert "&lt;" in html and "&amp;" in html


def test_application_json_has_the_specified_shape():
    posting_md = ("# Design Engineer — Runway\n\n- id: `ashby:x:1`\n"
                  "- location: Remote\n- apply: https://apply/here\n")
    data = application_json(posting_md, RESUME)
    assert data["company"] == "Runway"
    assert data["role_title"] == "Design Engineer"
    assert data["apply_url"] == "https://apply/here"
    assert data["roles"][0]["employer"] == "Acme"
    assert data["roles"][0]["bullets"] == ["Shipped the design system."]
    assert "Design systems" in data["skills"]


def test_application_json_reflects_edits_to_the_markdown():
    """The whole point of rendering from the edited file."""
    edited = RESUME.replace("Shipped the design system.", "Rewrote it myself.")
    data = application_json("# R — C\n\n- id: `x`\n", edited)
    assert data["roles"][0]["bullets"] == ["Rewrote it myself."]


def test_application_json_is_serialisable():
    json.dumps(application_json("# R — C\n\n- id: `x`\n", RESUME))


# --- document titles drive the PDF filename --------------------------------
# Chrome's "Save as PDF" suggests a filename from the HTML <title>, so the
# title IS the filename a recruiter sees. Convention, his words:
#   "Sam Example - Resume - Runway - Design Engineer"  (no em-dashes)

from jobhunt.generation.deliver import document_title


def test_the_resume_title_follows_the_convention():
    assert document_title("Resume", "Sam Example", "Runway",
                          "Design Engineer") == (
        "Sam Example - Resume - Runway - Design Engineer")


def test_the_cover_letter_title_follows_the_convention():
    assert document_title("Cover Letter", "Sam Example", "Klaviyo",
                          "Senior Product Designer") == (
        "Sam Example - Cover Letter - Klaviyo - Senior Product Designer")


def test_no_em_dash_or_en_dash_survives_anywhere():
    """He asked for plain hyphens. An em-dash in a filename also trips some
    ATS upload forms, which is the second reason to normalise it."""
    t = document_title("Resume", "Sam Example", "Ai2 — Allen Institute",
                       "Lead Product Designer – AI")
    assert "—" not in t and "–" not in t
    assert t == ("Sam Example - Resume - Ai2 - Allen Institute - "
                 "Lead Product Designer - AI")


def test_a_missing_role_drops_the_segment_rather_than_printing_a_placeholder():
    """`posting.md` used to record the literal string 'Role' because
    `posting_markdown` read `role_title` off a `Posting`, which has no such
    field. A placeholder in a filename a recruiter reads is worse than an
    absent segment."""
    assert document_title("Resume", "Sam Example", "Runway", None) == (
        "Sam Example - Resume - Runway")
    assert document_title("Resume", "Sam Example", "Runway", "Role") == (
        "Sam Example - Resume - Runway")
    assert document_title("Resume", "Sam Example", None, None) == (
        "Sam Example - Resume")


def test_characters_illegal_in_a_filename_are_removed():
    """The title becomes a filename. A slash would create a directory."""
    t = document_title("Resume", "Sam Example", "Ai2 / Allen Institute",
                       "Designer: AI")
    assert "/" not in t and ":" not in t


def test_whitespace_is_collapsed():
    assert document_title("Resume", "Sam  Example", " Runway ",
                          "Design   Engineer") == (
        "Sam Example - Resume - Runway - Design Engineer")


def test_company_and_role_come_from_explicit_fields_not_the_heading():
    """The H1 is a DISPLAY string. Parsing it meant the reader and the writer
    had to agree on a separator character, and they silently stopped agreeing
    the moment the writer's em-dash became a hyphen - company and role both
    went missing from the PDF filename. Explicit fields cannot drift."""
    from jobhunt.generation.deliver import application_json
    md = ("# Design Engineer - Runway\n\n"
          "- company: Runway\n- role: Design Engineer\n"
          "- id: `ashby:runway-ml:abc`\n- location: Remote\n")
    out = application_json(md, "# Sam Example\n")
    assert out["company"] == "Runway"
    assert out["role_title"] == "Design Engineer"


def test_a_role_containing_a_hyphen_survives():
    """`Senior Engineer - Platform` would break any separator-splitting
    parser; explicit fields do not care."""
    from jobhunt.generation.deliver import application_json
    md = ("# Senior Engineer - Platform - Acme\n\n"
          "- company: Acme\n- role: Senior Engineer - Platform\n")
    out = application_json(md, "# Sam Example\n")
    assert out["company"] == "Acme"
    assert out["role_title"] == "Senior Engineer - Platform"


def test_a_legacy_em_dash_heading_still_parses():
    """Three application directories on disk predate the explicit fields."""
    from jobhunt.generation.deliver import application_json
    out = application_json("# Design Engineer — Runway\n", "# Sam Example\n")
    assert out["company"] == "Runway"
    assert out["role_title"] == "Design Engineer"


# --- no em-dashes anywhere in a generated document -------------------------
# His instruction, verbatim: "no em dashses anywhere please". Three sources
# feed these documents - the templates' own separators, the model's prose, and
# his OWN profile bullets, which carry em-dashes from his portfolio HTML.
# Normalising at document-build time covers all three without rewriting the
# source profile, which is his words and not ours to edit.

from jobhunt.generation.deliver import plain_dashes


@pytest.mark.parametrize("ch", ["‒", "–", "—", "―",
                                "‐", "‑", "−"])
def test_every_unicode_dash_becomes_a_plain_hyphen(ch):
    assert plain_dashes(f"a{ch}b") == "a-b"


def test_spacing_around_a_dash_is_preserved():
    """'the API — specialist routing' must not become 'the API-specialist'."""
    assert plain_dashes("the API — specialist routing") == (
        "the API - specialist routing")


def test_an_ordinary_hyphen_is_untouched():
    assert plain_dashes("Designer-turned-builder") == "Designer-turned-builder"


def test_plain_dashes_tolerates_none_and_empty():
    assert plain_dashes(None) == ""
    assert plain_dashes("") == ""


def test_a_slash_in_a_role_becomes_a_separator_not_a_deletion():
    """REVIEW MINOR 2. `/` was being DELETED, so the very plausible role
    'UX/UI Designer' rendered as 'UXUI Designer' in the filename."""
    t = document_title("Resume", "Sam Example", "Acme", "UX/UI Designer")
    assert "UXUI" not in t
    assert t == "Sam Example - Resume - Acme - UX-UI Designer"


def test_an_empty_name_does_not_leave_a_leading_separator():
    assert document_title("Resume", "", "Acme", "Designer") == (
        "Resume - Acme - Designer")


def test_a_trailing_dot_is_stripped_from_a_segment():
    """'Designer.' would give 'Designer..pdf'."""
    assert document_title("Resume", "Sam Example", "Acme",
                          "Designer.") .endswith("Designer")


@pytest.mark.parametrize("junk", ["(no title)", "?", "Role", "untitled"])
def test_every_known_placeholder_is_dropped(junk):
    """REVIEW MINOR 5. `(no title)` is store.py's COALESCE default and `?` is
    match.py's. Either can reach a document."""
    assert document_title("Resume", "Sam Example", "Acme", junk) == (
        "Sam Example - Resume - Acme")
