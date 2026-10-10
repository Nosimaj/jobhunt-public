"""`python -m jobhunt.generate <posting_id>`."""

import json
from pathlib import Path

import pytest

from jobhunt.discovery.models import Posting
from jobhunt.discovery.store import Store
from jobhunt.generate.cli import main
from jobhunt.generation.models import (ChosenBullet, CoverLetter,
                                       ResumeSelection)
from jobhunt.matching.models import RoleVerdict

PROFILE = Path("tests/fixtures/profile_golden.json")
pytestmark = pytest.mark.skipif(
    not PROFILE.is_file(),
    reason="run `python -m jobhunt.ingest` first")


def posting(pid="ashby:runway-ml:1", company="Runway"):
    return Posting(id=pid, source="ashby", source_thread_id="runway-ml",
                   company=company, company_normalised=company.casefold(),
                   raw_text="Design Engineer. " + "duties " * 80,
                   apply_url="https://apply/here", is_remote=True,
                   geo_signal="us", posted_at="2026-09-01", first_seen="x",
                   last_seen="x", status="new")


VERDICT = RoleVerdict(role_title="Design Engineer",
                      discipline="design_engineering", reason="fits",
                      hybrid_test="pass", domain_fit="none", hybrid_evidence=["usability testing"],
                      overlaps=["design systems"], gaps=[], role_fit="strong")


class FakeBlock:
    def __init__(self, p): self.type = "text"; self.parsed_output = p
class FakeMsg:
    def __init__(self, p):
        self.content = [FakeBlock(p)]; self.stop_reason = "end_turn"
        self.usage = type("U", (), {"input_tokens": 1, "output_tokens": 1,
                                    "cache_read_input_tokens": 0})()
class FakeMessages:
    def __init__(self): self.calls = []
    def parse(self, **kw):
        self.calls.append(kw)
        if kw["output_format"] is ResumeSelection:
            prof = json.loads(PROFILE.read_text())
            first = next(b["id"] for r in prof["roles"]
                         if r.get("render") == "feature"
                         for b in r["bullets"]
                         if b["audience"] != "untagged" and b["user_approved"])
            text = next(b["text"] for r in prof["roles"]
                        for b in r["bullets"] if b["id"] == first)
            return FakeMsg(ResumeSelection(bullets=[ChosenBullet(
                bullet_id=first, rewritten=text, answers="relevant")]))
        return FakeMsg(CoverLetter(paragraphs=["I would like to apply."]))
class FakeClient:
    def __init__(self): self.messages = FakeMessages()


def seed(tmp_path, p=None):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    p = p or posting()
    s.upsert_posting(p)
    s.save_role_verdict(p.id, VERDICT, model="m", prompt_sha="s")
    return s


def run(tmp_path, *extra, client=None):
    return main(["--db", str(tmp_path / "j.sqlite"),
                 "--out", str(tmp_path / "apps"), *extra],
                client=client or FakeClient())


def test_generate_writes_the_three_files(tmp_path):
    seed(tmp_path)
    assert run(tmp_path, "ashby:runway-ml:1") == 0
    d = next((tmp_path / "apps").iterdir())
    assert {f.name for f in d.iterdir()} == {
        "resume.md", "cover-letter.md", "posting.md"}


def test_the_resume_contains_a_real_bullet(tmp_path):
    seed(tmp_path)
    run(tmp_path, "ashby:runway-ml:1")
    md = next((tmp_path / "apps").rglob("resume.md")).read_text()
    assert "## Experience" in md and md.count("\n- ") >= 1


def test_a_posting_with_no_verdict_fails_loudly(tmp_path):
    """REVIEW-CRITICAL. Generation reuses the verdict and must never quietly
    generate from the posting text alone."""
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    s.upsert_posting(posting())
    assert run(tmp_path, "ashby:runway-ml:1") == 1


def test_an_unknown_posting_id_fails_loudly(tmp_path):
    seed(tmp_path)
    assert run(tmp_path, "ashby:nope:9") == 1


def test_rerunning_refuses_to_overwrite_edits(tmp_path):
    """REVIEW-CRITICAL. His edit IS the approval, so silently overwriting it
    destroys approved work."""
    seed(tmp_path)
    run(tmp_path, "ashby:runway-ml:1")
    resume = next((tmp_path / "apps").rglob("resume.md"))
    resume.write_text("MY EDITED VERSION\n", encoding="utf-8")

    assert run(tmp_path, "ashby:runway-ml:1") == 1
    assert resume.read_text() == "MY EDITED VERSION\n"


def test_force_overwrites_deliberately(tmp_path):
    seed(tmp_path)
    run(tmp_path, "ashby:runway-ml:1")
    resume = next((tmp_path / "apps").rglob("resume.md"))
    resume.write_text("MY EDITED VERSION\n", encoding="utf-8")

    assert run(tmp_path, "ashby:runway-ml:1", "--force") == 0
    assert resume.read_text() != "MY EDITED VERSION\n"


def test_two_postings_at_one_company_get_separate_directories(tmp_path):
    """REVIEW-CRITICAL. Runway has five open design roles; collapsing them to
    one directory would silently overwrite an application."""
    s = seed(tmp_path)
    p2 = posting("ashby:runway-ml:2")
    s.upsert_posting(p2)
    v2 = VERDICT.model_copy(update={"role_title": "Product Designer"})
    s.save_role_verdict(p2.id, v2, model="m", prompt_sha="s")

    run(tmp_path, "ashby:runway-ml:1")
    run(tmp_path, "ashby:runway-ml:2")

    assert len(list((tmp_path / "apps").iterdir())) == 2


def test_nothing_is_written_to_the_database(tmp_path):
    s = seed(tmp_path)
    before = s.get("ashby:runway-ml:1").model_dump()
    run(tmp_path, "ashby:runway-ml:1")
    assert Store(tmp_path / "j.sqlite").get("ashby:runway-ml:1").model_dump() == before


def test_render_turns_edited_markdown_into_deliverables(tmp_path):
    seed(tmp_path)
    run(tmp_path, "ashby:runway-ml:1")
    d = next((tmp_path / "apps").iterdir())
    (d / "resume.md").write_text(
        (d / "resume.md").read_text() + "\n- My own added line.\n",
        encoding="utf-8")

    assert main(["--render", str(d)]) == 0

    assert (d / "resume.txt").is_file()
    assert (d / "resume.html").is_file()
    assert (d / "application.json").is_file()
    assert "My own added line." in (d / "resume.txt").read_text()


def test_render_and_a_posting_id_together_are_rejected(tmp_path):
    with pytest.raises(SystemExit):
        main(["--render", str(tmp_path), "ashby:x:1"])


def test_render_titles_the_documents_for_the_pdf_filename(tmp_path):
    """REVIEW MINOR 7. The <title> IS the suggested PDF filename in Chrome, and
    it was the whole point of the change, yet nothing tested the wiring."""
    from jobhunt.generate.cli import _render
    (tmp_path / "resume.md").write_text("# Sam Example\n\n## Summary\n\nx\n",
                                        encoding="utf-8")
    (tmp_path / "cover-letter.md").write_text("Dear Team,\n\nx\n", encoding="utf-8")
    (tmp_path / "posting.md").write_text(
        "# Design Engineer - Runway\n\n- company: Runway\n- role: Design Engineer\n",
        encoding="utf-8")

    _render(tmp_path)

    assert ("<title>Sam Example - Resume - Runway - Design Engineer</title>"
            in (tmp_path / "resume.html").read_text(encoding="utf-8"))
    assert ("<title>Sam Example - Cover Letter - Runway - Design Engineer"
            "</title>" in (tmp_path / "cover-letter.html").read_text(encoding="utf-8"))


def test_render_without_an_h1_does_not_title_the_document_resume_resume(tmp_path):
    from jobhunt.generate.cli import _render
    (tmp_path / "resume.md").write_text("## Summary\n\nx\n", encoding="utf-8")
    (tmp_path / "posting.md").write_text(
        "# R - Acme\n\n- company: Acme\n- role: Designer\n", encoding="utf-8")

    _render(tmp_path)

    html = (tmp_path / "resume.html").read_text(encoding="utf-8")
    assert "<title>Resume - Acme - Designer</title>" in html


# --- an unrendered directory is incomplete, not finished -------------------
# 2026-10-07: nine applications were created with the bare `generate` CLI,
# which runs only the generate pass. `--render` never ran, so no .html
# existed. `application_state` keys readiness on resume.html, so the dashboard
# saw "not ready" and tried to generate; `generate` saw a directory and
# refused. That state was unreachable by any recovery the user could perform -
# the exact wedge CLAUDE.md records as CRITICAL-1 from the slice-5 review.

def test_generate_completes_an_unrendered_directory_instead_of_refusing(tmp_path, capsys):
    """Three markdown files and no HTML means the generate pass ran and the
    render pass did not. Finishing the job costs nothing and no API call."""
    from jobhunt.generate.cli import main
    d = tmp_path / "acme-designer"
    d.mkdir()
    (d / "resume.md").write_text("# Sam Example\n\n## Summary\n\nx\n",
                                 encoding="utf-8")
    (d / "cover-letter.md").write_text("Dear Team,\n\nx\n", encoding="utf-8")
    (d / "posting.md").write_text(
        "# Designer - Acme\n\n- company: Acme\n- role: Designer\n", encoding="utf-8")

    rc = main(["--render", str(d)])

    assert rc == 0
    assert (d / "resume.html").is_file()
    assert (d / "cover-letter.html").is_file()


def test_an_unrendered_directory_is_not_reported_as_ready(tmp_path):
    """`application_state` must agree with the CLI about what 'done' means, or
    the dashboard offers links to files that 404."""
    from jobhunt.dashboard.actions import application_state
    d = tmp_path / "acme-designer"
    d.mkdir()
    (d / "resume.md").write_text("x", encoding="utf-8")
    assert application_state(d) != "ready"

    (d / "resume.html").write_text("x", encoding="utf-8")
    (d / "cover-letter.html").write_text("x", encoding="utf-8")
    assert application_state(d) == "ready"
