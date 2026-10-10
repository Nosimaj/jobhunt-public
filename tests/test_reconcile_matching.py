from pathlib import Path

import pytest

from jobhunt.models import SourceRef
from jobhunt.overrides import Overrides, load_overrides
from jobhunt.parse_linkedin_pdf import normalize_lines, parse_text
from jobhunt.models import ResumeProfile
from jobhunt.reconcile import reconcile

FIXTURES = Path(__file__).parent / "fixtures"
SOURCES = [SourceRef(role="pdf", path="/x/Profile.pdf", sha256="a" * 64,
                     snapshot_date="2026-09-19"),
           SourceRef(role="resume", path="/x/resume.html", sha256="b" * 64,
                     snapshot_date="2026-09-19")]

# Minimal stand-in for the PDF's header region, for the synthetic-input tests
# below. reconcile() requires pdf_header_lines, so there is no way to build a
# Profile with a blank identity by accident.
SYNTHETIC_HEADER = [
    "www.linkedin.com/in/someone (LinkedIn)",
    "someone.example.com/ (Portfolio)",
    "someone@example.com",
    "Ada Example",
    "Engineer",
    "Boston, Massachusetts, United States",
    "Summary",
]


@pytest.fixture(scope="module")
def built():
    raw = (FIXTURES / "pdf_text_real.txt").read_text(encoding="utf-8")
    pdf_roles, pdf_conflicts = parse_text(raw)
    html = ResumeProfile.model_validate_json((FIXTURES / "resume_profile.json").read_text())
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    profile, report = reconcile(pdf_roles, html, ov, SOURCES,
                                pdf_header_lines=normalize_lines(raw),
                                today="2026-09-19", pdf_conflicts=pdf_conflicts)
    return profile, report


def test_all_twenty_pdf_roles_plus_one_manual(built):
    profile, _ = built
    assert len(profile.roles) == 21


def test_dates_always_come_from_the_pdf(built):
    profile, _ = built
    tidewater = next(r for r in profile.roles if r.company == "Tidewater Corporation")
    assert (tidewater.start, tidewater.end) == ("2016-12", "2019-11")
    swr = next(r for r in profile.roles if r.company == "Spokewell Repair")
    assert swr.end == "present"


def test_titles_are_kept_as_two_fields(built):
    profile, _ = built
    tidewater = next(r for r in profile.roles if r.company == "Tidewater Corporation")
    assert tidewater.title_of_record == "Sr Product Designer & Prototyper"
    assert tidewater.title_presented == "UX Designer — Connected Products"


def test_title_presented_falls_back_to_record_when_no_html_match(built):
    profile, _ = built
    wf = next(r for r in profile.roles if r.company == "Initech Bank")
    assert wf.title_presented == wf.title_of_record


def test_bullet_prose_comes_from_html_where_matched(built):
    """Matched roles lead with HTML bullets (reviewed, correctly voiced).

    The PDF bullets for these roles are appended after, not discarded -- see
    `test_pdf_bullets_are_appended_after_html_for_matched_roles` below.
    """
    profile, _ = built
    tidewater = next(r for r in profile.roles if r.company == "Tidewater Corporation")
    html_bullets = [b for b in tidewater.bullets if b.provenance.source == "resume"]
    assert len(html_bullets) == 4
    assert tidewater.bullets[:4] == html_bullets
    assert any(b.audience == "eng" for b in html_bullets)


# The 8 roles present in both sources. Distribution of APPENDED pdf bullets
# measured from the real fixtures (2026-09-23): the matched-role reconciler
# used to discard these entirely.
_MATCHED_ROLE_APPENDED_PDF_COUNTS = {
    "Spokewell Repair": 4,
    "Quillon Labs": 6,
    "Cartwell Health": 4,
    "Lumen Devices": 6,
    "Fernway Solutions": 3,
    "Tidewater Corporation": 5,
    "Fabrikam Mobile": 2,
    "Contoso Arcadia": 1,
}


def test_pdf_bullets_are_appended_after_html_for_matched_roles(built):
    """Matched roles keep BOTH sources' bullets: html first, pdf appended.

    Appended pdf bullets carry `audience="untagged"` (never reviewed) and
    `provenance.source="pdf"`, and their ids continue the numbering after the
    html bullets rather than restarting at `-b1`.
    """
    profile, _ = built
    by_company = {r.company: r for r in profile.roles}
    assert set(_MATCHED_ROLE_APPENDED_PDF_COUNTS) <= set(by_company)

    total_appended = 0
    for company, expected_pdf_count in _MATCHED_ROLE_APPENDED_PDF_COUNTS.items():
        role = by_company[company]
        html_bullets = [b for b in role.bullets if b.provenance.source == "resume"]
        pdf_bullets = [b for b in role.bullets if b.provenance.source == "pdf"]
        assert len(pdf_bullets) == expected_pdf_count, company
        # html first, pdf appended -- never interleaved, never leading.
        assert role.bullets == html_bullets + pdf_bullets, company
        assert all(b.audience == "untagged" for b in pdf_bullets), company
        assert all(not b.user_approved for b in pdf_bullets), company
        # ids continue the numbering rather than restarting at b1.
        expected_ids = [f"{role.id}-b{n}"
                        for n in range(1, len(role.bullets) + 1)]
        assert [b.id for b in role.bullets] == expected_ids, company
        total_appended += len(pdf_bullets)

    assert total_appended == 31


def test_all_bullet_ids_in_the_profile_are_unique(built):
    """Appending pdf bullets after html ones must never collide an id.

    Ids are `{role_id}-b{n}`; a bug that restarts pdf numbering at 1 instead
    of continuing after the html bullets would silently collide with an
    existing html bullet id.
    """
    profile, _ = built
    ids = [b.id for r in profile.roles for b in r.bullets]
    assert len(ids) == len(set(ids)), "duplicate bullet id found"


def test_pdf_only_roles_are_untagged_and_never_both(built):
    profile, _ = built
    pdf_only = [r for r in profile.roles
                if r.bullets and r.bullets[0].provenance.source == "pdf"]
    assert len(pdf_only) == 12
    audiences = {b.audience for r in pdf_only for b in r.bullets}
    assert audiences == {"untagged"}


def test_no_pdf_bullet_is_ever_marked_both(built):
    profile, _ = built
    assert not any(b.provenance.source == "pdf" and b.audience == "both"
                   for r in profile.roles for b in r.bullets)


def test_alias_resolves_brightwater_to_point_road(built):
    profile, _ = built
    assert any(r.company == "Fernway Solutions" for r in profile.roles)
    assert not any(r.company.startswith("Brightwater") for r in profile.roles)


def test_summaries_and_skills_come_from_html(built):
    profile, _ = built
    assert profile.summaries.design and profile.summaries.eng
    assert len(profile.skills) == 15
    assert sum(1 for s in profile.skills if s.audience == "eng") == 5


def test_identity_comes_from_pdf_header(built):
    profile, _ = built
    assert profile.identity.name == "Sam Example"
    assert profile.identity.email == "sam@example.com"
    assert "&amp;" not in profile.summaries.design


def test_identity_links_are_found_by_their_parenthetical_labels(built):
    """All three keys spec §4 declares must be present, `personal` included.

    `personal` never populated before: the old branch required the substring
    "portfolio" in the line, which `samexample.dev/ (Personal)` does not
    contain, so the key silently went missing.
    """
    profile, _ = built
    links = profile.identity.links
    assert set(links) == {"linkedin", "portfolio", "personal"}
    assert links["linkedin"] == "www.linkedin.com/in/sam-example"
    assert links["portfolio"] == "samexample.dev/portfolio"
    assert links["personal"] == "samexample.dev/"


def test_identity_links_survive_extra_contact_lines():
    """A grown contact block must not push any link out of range.

    Every label below sits at index >= 11, i.e. outside the `lines[:10]`
    window the old implementation read. The labels also appear in both of the
    exporter's forms: on their own line below the URL, and inline after it.
    An unrelated `(Mobile)` parenthetical must be ignored.
    """
    from jobhunt.models import ResumeProfile, PdfRole, Summaries
    header = [
        "Contact",
        "+1 555 010 0000 (Mobile)",        # extra line the old window never saw
        "someone@example.com",
        "Top Skills",
        "AI Supile", "Analytics", "A/B Bagezav", "Prototyping",
        "Design Systems", "Research Ops",
        "Languages", "English",
        "www.linkedin.com/in/someone",
        "(LinkedIn)",                      # label on its own line
        "someone.example.com/portfolio",
        "(Portfolio)",
        "someone.example.com/ (Personal)",  # label inline
        "Ada Example",
        "Engineer",
        "Boston, Massachusetts, United States",
        "Summary",
    ]
    assert all(lbl not in "".join(header[:10])
               for lbl in ("(LinkedIn)", "(Portfolio)", "(Personal)"))

    pdf = [PdfRole(company="Acme", title="Engineer", start="2020-01",
                   end="2020-06", duration_text="6 months", bullets=["a."])]
    html = ResumeProfile(summaries=Summaries(design="d", eng="e"), skills=[], jobs=[])
    profile, _ = reconcile(pdf, html, Overrides(), SOURCES,
                           pdf_header_lines=header, today="2026-09-19")

    assert profile.identity.links == {
        "linkedin": "www.linkedin.com/in/someone",
        "portfolio": "someone.example.com/portfolio",
        "personal": "someone.example.com/",
    }
    # The anchored name/headline/location reads are unaffected by the extra line.
    assert profile.identity.name == "Ada Example"
    assert profile.identity.email == "someone@example.com"


def test_title_disagreements_are_reported_as_informational(built):
    _, report = built
    assert any("Tidewater" in c for c in report.conflicts)


def test_role_ids_are_unique_and_stable(built):
    profile, _ = built
    ids = [r.id for r in profile.roles]
    assert len(ids) == len(set(ids))
    assert "tidewater-corporation-2016-12" in ids


def test_ambiguous_match_is_reported_not_guessed():
    # Two PDF engagements at one employer, one HTML job: the tie must be reported.
    from jobhunt.models import ResumeBullet, ResumeJob, ResumeProfile, PdfRole, Summaries
    pdf = [PdfRole(company="Acme", title="Engineer I", start="2020-01",
                   end="2020-06", duration_text="6 months", bullets=["a."]),
           PdfRole(company="Acme", title="Engineer II", start="2020-07",
                   end="2020-12", duration_text="6 months", bullets=["b."])]
    html = ResumeProfile(summaries=Summaries(design="d", eng="e"), skills=[],
                       jobs=[ResumeJob(org="Acme", title="Engineer",
                                     date_placeholder=True,
                                     bullets=[ResumeBullet(text="c.", audience="both")])])
    profile, report = reconcile(pdf, html, Overrides(), SOURCES,
                                pdf_header_lines=SYNTHETIC_HEADER,
                                today="2026-09-19")
    assert any("ambiguous" in c.lower() and "Acme" in c for c in report.conflicts)


def test_duplicate_html_job_match_is_reported_not_overwritten():
    """Two HTML jobs, ONE PDF role at that employer.

    The collision must be reported and the second HTML job must not silently
    overwrite the first. The two HTML titles are deliberately NOT prefixes of
    one another ("Engineer" / "Architect"): with prefix-overlapping titles a
    substring assertion is satisfied by the unrelated *title-disagreement*
    conflict, so the test would pass even with the collision guard removed.
    The assertions below therefore key on a phrase unique to the COLLISION
    message and on WHICH bullet survived, by text.

    The role still matched a PDF counterpart (the first HTML job), so its PDF
    bullet is appended after the surviving HTML bullet, not displaced.
    """
    from jobhunt.models import ResumeBullet, ResumeJob, ResumeProfile, PdfRole, Summaries
    first_bullet = "Shipped the billing platform."
    second_bullet = "Redesigned the onboarding flow."
    pdf_bullet = "PDF prose that is appended, not displaced."
    pdf = [PdfRole(company="Acme", title="Engineer", start="2020-01",
                   end="2020-06", duration_text="6 months",
                   bullets=[pdf_bullet])]
    html = ResumeProfile(
        summaries=Summaries(design="d", eng="e"), skills=[],
        jobs=[ResumeJob(org="Acme", title="Engineer", date_placeholder=True,
                      bullets=[ResumeBullet(text=first_bullet, audience="both")]),
              ResumeJob(org="Acme", title="Architect", date_placeholder=True,
                      bullets=[ResumeBullet(text=second_bullet, audience="both")])])
    profile, report = reconcile(pdf, html, Overrides(), SOURCES,
                                pdf_header_lines=SYNTHETIC_HEADER,
                                today="2026-09-19")

    # "both match PDF role" appears only in the collision message; the
    # title-disagreement message reads "title differs for ...".
    collisions = [c for c in report.conflicts if "both match PDF role" in c]
    assert len(collisions) == 1, report.conflicts
    assert "Acme / Engineer" in collisions[0]
    assert "Acme / Architect" in collisions[0]

    # The FIRST match wins; the second must not overwrite it. The pdf bullet
    # is still appended after it.
    acme_role = next(r for r in profile.roles if r.company == "Acme")
    assert [b.text for b in acme_role.bullets] == [first_bullet, pdf_bullet]
    pdf_result = acme_role.bullets[1]
    assert pdf_result.provenance.source == "pdf"
    assert pdf_result.audience == "untagged"
    assert pdf_result.id == f"{acme_role.id}-b2"


def test_role_ids_are_independent_of_pdf_input_order():
    """Shuffling the PDF input must not change WHICH role gets which id.

    Comparing the SET of ids proves nothing: the same ids are handed out under
    any assignment, so a set comparison holds even if two same-month roles at
    one employer swap identities. What matters is the id-to-role PAIRING, so
    this compares full mappings across two independent shuffles.
    """
    import random

    raw = (FIXTURES / "pdf_text_real.txt").read_text(encoding="utf-8")
    pdf_roles, pdf_conflicts = parse_text(raw)
    html = ResumeProfile.model_validate_json((FIXTURES / "resume_profile.json").read_text())
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    header_lines = normalize_lines(raw)

    def pairing(roles):
        return {r.id: (r.company, r.start, r.end, r.title_of_record) for r in roles}

    def build(order):
        profile, _ = reconcile(order, html, ov, SOURCES,
                               pdf_header_lines=header_lines,
                               today="2026-09-19",
                               pdf_conflicts=pdf_conflicts)
        return profile

    baseline = pairing(build(pdf_roles).roles)
    # The fixture contains two Contoso engagements starting 2011-11, which is
    # the only id collision in the real data and therefore the only place a
    # pairing can drift.
    assert sum(1 for k in baseline if k.startswith("contoso-2011-11")) == 2

    for seed in (42, 7):
        shuffled = list(pdf_roles)
        random.Random(seed).shuffle(shuffled)
        assert pairing(build(shuffled).roles) == baseline, f"seed {seed}"


def test_role_order_is_identical_across_shuffles():
    """`roles` must be byte-reproducible, so its ORDER must be shuffle-proof.

    `_sort_key` used to key only on `start`, leaving Python's stable sort to
    fall back on PDF input order. Measured before the fix: 17 of these 25
    seeds reordered the two `contoso-2011-11` roles. Asserting the id SET is
    not enough here -- the order is what lands in the file.
    """
    import random

    raw = (FIXTURES / "pdf_text_real.txt").read_text(encoding="utf-8")
    pdf_roles, pdf_conflicts = parse_text(raw)
    html = ResumeProfile.model_validate_json((FIXTURES / "resume_profile.json").read_text())
    ov = load_overrides(FIXTURES / "overrides_synthetic.yaml")
    header_lines = normalize_lines(raw)

    def order(roles):
        profile, _ = reconcile(roles, html, ov, SOURCES,
                               pdf_header_lines=header_lines,
                               today="2026-09-19", pdf_conflicts=pdf_conflicts)
        return [r.id for r in profile.roles]

    baseline = order(pdf_roles)
    assert len(baseline) == 21
    # The tie that used to break: two roles sharing a start month.
    assert baseline.count("contoso-2011-11") == 1
    assert baseline.count("contoso-2011-11-2") == 1

    for seed in range(25):
        shuffled = list(pdf_roles)
        random.Random(seed).shuffle(shuffled)
        assert order(shuffled) == baseline, f"seed {seed} reordered roles"


def test_identity_raises_when_summary_line_missing():
    from jobhunt.models import ResumeProfile, PdfRole, Summaries
    pdf = [PdfRole(company="Acme", title="Engineer", start="2020-01",
                   end="2020-06", duration_text="6 months", bullets=["a."])]
    html = ResumeProfile(summaries=Summaries(design="d", eng="e"), skills=[], jobs=[])
    with pytest.raises(ValueError, match="Summary"):
        reconcile(pdf, html, Overrides(), SOURCES,
                  pdf_header_lines=["Contact", "no-summary-here"],
                  today="2026-09-19")


def test_reconcile_requires_pdf_header_lines():
    """Omitting the header lines must be a TypeError, not a blank identity."""
    from jobhunt.models import ResumeProfile, PdfRole, Summaries
    pdf = [PdfRole(company="Acme", title="Engineer", start="2020-01",
                   end="2020-06", duration_text="6 months", bullets=["a."])]
    html = ResumeProfile(summaries=Summaries(design="d", eng="e"), skills=[], jobs=[])
    with pytest.raises(TypeError, match="pdf_header_lines"):
        reconcile(pdf, html, Overrides(), SOURCES, today="2026-09-19")  # type: ignore[call-arg]


def test_empty_pdf_header_lines_raise_rather_than_blanking_identity():
    from jobhunt.models import ResumeProfile, PdfRole, Summaries
    pdf = [PdfRole(company="Acme", title="Engineer", start="2020-01",
                   end="2020-06", duration_text="6 months", bullets=["a."])]
    html = ResumeProfile(summaries=Summaries(design="d", eng="e"), skills=[], jobs=[])
    with pytest.raises(ValueError, match="no PDF header lines"):
        reconcile(pdf, html, Overrides(), SOURCES, pdf_header_lines=[],
                  today="2026-09-19")
