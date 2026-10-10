"""Markdown rendering. Pure - no model, no files."""

from jobhunt.generation.models import AcceptedBullet
from jobhunt.generation.render import (cover_letter_markdown,
                                       posting_markdown, resume_markdown, slug)

IDENTITY = {"name": "Sam Example", "headline": "Designer who builds",
            "location": "Boston, MA", "email": "e@example.com",
            "links": {"portfolio": "https://example.com"}}


def role(company="Acme", render="feature", presented="Senior Designer",
         of_record="Designer"):
    return {"id": company.lower(), "company": company,
            "title_of_record": of_record, "title_presented": presented,
            "start": "2020-01", "end": "2021-06", "render": render,
            "bullets": []}


def profile(roles):
    return {"identity": IDENTITY, "summaries": {"design": "D", "eng": "E"},
            "skills": [{"text": "Design systems", "audience": "design"}],
            "roles": roles, "display_precision": "years"}


def test_the_presented_title_is_rendered_not_the_title_of_record():
    p = profile([role(presented="Principal Product Designer",
                      of_record="Sr Experience Designer")])
    md = resume_markdown(p, p["roles"], {}, "design")
    assert "Principal Product Designer" in md
    assert "Sr Experience Designer" not in md


def test_years_precision_hides_the_month():
    p = profile([role()])
    md = resume_markdown(p, p["roles"], {}, "design")
    assert "2020 - 2021" in md
    assert "2020-01" not in md


def test_a_minimal_role_renders_header_only():
    """REVIEW-CRITICAL: with eligible bullets present, so it cannot pass by
    accident."""
    r = role("Quiet", render="minimal")
    p = profile([r])
    accepted = {"quiet": [AcceptedBullet(bullet_id="b1", text="Secret work.")]}
    md = resume_markdown(p, p["roles"], accepted, "design")
    assert "Quiet" in md
    assert "Secret work." not in md


def test_a_feature_role_renders_its_accepted_bullets():
    p = profile([role("Acme")])
    accepted = {"acme": [AcceptedBullet(bullet_id="b1", text="Shipped it.")]}
    assert "Shipped it." in resume_markdown(p, p["roles"], accepted, "design")


def test_roles_render_newest_first():
    old, new = role("Older"), role("Newer")
    old["start"], old["end"] = "2010-01", "2011-01"
    p = profile([old, new])
    md = resume_markdown(p, p["roles"], {}, "design")
    assert md.index("Newer") < md.index("Older")


def test_a_present_role_renders_as_present():
    r = role("Now"); r["end"] = "present"
    p = profile([r])
    assert "Present" in resume_markdown(p, p["roles"], {}, "design")


def test_the_cover_letter_has_no_salutation_or_signoff_from_the_model():
    from jobhunt.generation.models import CoverLetter
    letter = CoverLetter(paragraphs=["First para.", "Second para."])
    md = cover_letter_markdown(IDENTITY, "Runway", "Design Engineer", letter, [])
    assert "First para." in md and "Second para." in md
    assert "Sam Example" in md


def test_cited_case_studies_are_listed_with_their_urls():
    from jobhunt.generation.case_studies import CaseStudy
    from jobhunt.generation.models import CoverLetter
    cs = CaseStudy(title="Triage", url="https://x/y", pitch="p", lens="design")
    letter = CoverLetter(paragraphs=["Body."], cited_case_studies=["Triage"])
    md = cover_letter_markdown(IDENTITY, "Runway", "DE", letter, [cs])
    assert "https://x/y" in md


def test_the_posting_snapshot_carries_the_apply_url():
    posting = {"id": "ashby:x:1", "company": "Runway", "role_title": "DE",
               "apply_url": "https://apply/here", "location": "Remote",
               "raw_text": "Job text here."}
    md = posting_markdown(posting, {"role_fit": "strong", "reason": "fits"})
    assert "https://apply/here" in md and "Job text here." in md


def test_slugs_are_filesystem_safe():
    assert slug("PermitFlow (YC W22)") == "permitflow-yc-w22"
    assert slug("Sr./Staff Product Designer, Growth") == \
        "sr-staff-product-designer-growth"


def test_slugs_do_not_collapse_two_different_roles_to_one():
    assert slug("Design Engineer") != slug("Design Engineer, Web")


# --- posting.md must carry company and role as EXPLICIT fields -------------

def test_posting_markdown_takes_the_role_title_from_the_VERDICT():
    """`Posting` has NO `role_title` field - verified against model_fields.
    Reading it from `posting` silently produced the literal string "Role" in
    every posting.md ever written, which then flowed into application.json and
    into the PDF filename a recruiter reads. This project has shipped
    `p.role_title` on a `Posting` once before."""
    from jobhunt.discovery.models import Posting
    assert "role_title" not in Posting.model_fields

    md = posting_markdown(
        {"id": "a:1", "company": "Runway", "raw_text": "x"},
        {"role_fit": "strong", "reason": "fits", "role_title": "Design Engineer"})
    assert "Design Engineer" in md.splitlines()[0]
    assert "Role" not in md.splitlines()[0]


def test_posting_markdown_emits_company_and_role_as_fields():
    """The H1 is a DISPLAY string. `application_json` used to parse it, which
    meant reader and writer had to agree on a separator character - and they
    silently stopped agreeing the moment one of them changed."""
    md = posting_markdown(
        {"id": "a:1", "company": "Runway", "raw_text": "x"},
        {"role_fit": "strong", "reason": "fits", "role_title": "Design Engineer"})
    assert "- company: Runway" in md
    assert "- role: Design Engineer" in md


def test_posting_markdown_omits_a_role_it_does_not_have():
    """No placeholder: an absent role must not become the string 'Role'."""
    md = posting_markdown({"id": "a:1", "company": "Runway", "raw_text": "x"},
                          {"role_fit": "no", "reason": "r"})
    assert "- role:" not in md


# --- no em-dashes reach a generated document -------------------------------

def test_the_resume_markdown_carries_no_unicode_dash():
    """Three sources feed it: the span separator, the model's prose, and his
    own profile bullets - his portfolio HTML is full of em-dashes."""
    p = profile([role()])
    p["summaries"]["design"] = "Shipped an API — end to end."
    md = resume_markdown(p, p["roles"], {}, "design")
    assert "—" not in md and "–" not in md


def test_the_cover_letter_markdown_carries_no_unicode_dash():
    from jobhunt.generation.models import CoverLetter
    letter = CoverLetter(paragraphs=["We shipped it — fast."],
                         cited_case_studies=[])
    md = cover_letter_markdown(IDENTITY, "Acme", "Design Engineer", letter, [])
    assert "—" not in md


def test_the_posting_markdown_carries_no_unicode_dash():
    md = posting_markdown(
        {"id": "a:1", "company": "Acme", "raw_text": "on-site — NYC"},
        {"role_fit": "strong", "reason": "fits — well",
         "role_title": "Design Engineer"})
    assert "—" not in md
