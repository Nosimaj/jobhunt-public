import hashlib

import pytest

from jobhunt.snapshot import snapshot


def _make(tmp_path):
    pdf = tmp_path / "Profile.pdf"
    html = tmp_path / "resume.html"
    pdf.write_bytes(b"%PDF-1.4 fake")
    html.write_text("<html></html>", encoding="utf-8")
    return {"pdf": pdf, "resume": html}


def test_copies_files_and_records_sha256(tmp_path):
    sources = _make(tmp_path)
    dest = tmp_path / "snap"
    manifest = snapshot(sources, dest, today="2026-09-19")
    assert manifest.date == "2026-09-19"
    assert (dest / "2026-09-19" / "Profile.pdf").read_bytes() == b"%PDF-1.4 fake"
    expected = hashlib.sha256(b"%PDF-1.4 fake").hexdigest()
    assert next(f.sha256 for f in manifest.files if f.role == "pdf") == expected


def test_is_idempotent_for_the_same_day(tmp_path):
    sources = _make(tmp_path)
    dest = tmp_path / "snap"
    first = snapshot(sources, dest, today="2026-09-19")
    second = snapshot(sources, dest, today="2026-09-19")
    assert [f.sha256 for f in first.files] == [f.sha256 for f in second.files]


def test_missing_source_raises_naming_the_path(tmp_path):
    sources = {"pdf": tmp_path / "Definitely-Absent.pdf"}
    with pytest.raises(FileNotFoundError, match="Definitely-Absent.pdf"):
        snapshot(sources, tmp_path / "snap", today="2026-09-19")


def test_records_original_path_not_snapshot_path(tmp_path):
    sources = _make(tmp_path)
    manifest = snapshot(sources, tmp_path / "snap", today="2026-09-19")
    assert next(f.path for f in manifest.files if f.role == "resume").endswith("resume.html")
    assert "snap" not in next(f.path for f in manifest.files if f.role == "resume")


def test_no_partial_snapshot_on_missing_source(tmp_path):
    """Validate all sources exist before copying any."""
    pdf = tmp_path / "Profile.pdf"
    html = tmp_path / "resume.html"
    pdf.write_bytes(b"%PDF-1.4 fake")
    # html does not exist
    sources = {"pdf": pdf, "resume": html}
    dest = tmp_path / "snap"
    with pytest.raises(FileNotFoundError, match="resume.html"):
        snapshot(sources, dest, today="2026-09-19")
    # Verify no files were copied
    snapshot_dir = dest / "2026-09-19"
    assert list(snapshot_dir.glob("*")) == []


def test_archival_roles_copied_but_not_in_manifest(tmp_path):
    """Archival roles are copied to disk but omitted from manifest."""
    pdf = tmp_path / "Profile.pdf"
    html = tmp_path / "resume.html"
    pdf_superseded = tmp_path / "SamExampleATS2026.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    html.write_text("<html></html>", encoding="utf-8")
    pdf_superseded.write_bytes(b"%PDF-1.4 superseded")
    sources = {"pdf": pdf, "resume": html, "pdf_superseded": pdf_superseded}
    dest = tmp_path / "snap"
    manifest = snapshot(sources, dest, today="2026-09-19")
    # All three files copied to disk
    assert (dest / "2026-09-19" / "Profile.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert (dest / "2026-09-19" / "resume.html").read_bytes() == b"<html></html>"
    assert (dest / "2026-09-19" / "SamExampleATS2026.pdf").read_bytes() == b"%PDF-1.4 superseded"
    # Only parsed roles in manifest
    assert len(manifest.files) == 2
    assert {f.role for f in manifest.files} == {"pdf", "resume"}
