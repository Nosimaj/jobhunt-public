"""Write a tailored application to disk for Sam to edit.

Generation and rendering are separate commands on purpose: their edit between
them is the approval, so this command must never destroy an edited file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import config
from ..discovery.store import Store
from ..env import EnvFileError, load_env
from ..generation.case_studies import load_case_studies
from ..generation.prompt import (build_bullet_user, build_letter_user,
                                 build_system)
from ..generation.render import (cover_letter_markdown, posting_markdown,
                                 resume_markdown, slug)
from ..generation.select import (eligible_bullets, lens_for,
                                 relevant_case_studies, renderable_roles)
from ..generation.writer import (Writer, WriteError, accept_selection,
                                 check_citations)

FILES = ("resume.md", "cover-letter.md", "posting.md")


class GenerateError(RuntimeError):
    """Fatal to this application. Printed once, exit 1."""


def _build_client(client):
    if client is not None:
        return client
    import anthropic
    return anthropic.Anthropic()


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobhunt.generate",
        description="Write a tailored resume and cover letter for one posting.")
    parser.add_argument("posting_id", nargs="?")
    parser.add_argument("--db", type=Path, default=config.JOBS_DB)
    parser.add_argument("--profile", type=Path, default=config.PROFILE_PATH)
    parser.add_argument("--out", type=Path, default=config.APPLICATIONS_DIR)
    parser.add_argument("--force", action="store_true",
                        help="overwrite files even if they have been edited")
    parser.add_argument("--model", default=None)
    parser.add_argument("--render", type=Path, default=None, metavar="DIR",
                        help="turn an edited application directory into "
                             "deliverables")
    args = parser.parse_args(argv)

    if args.render and args.posting_id:
        parser.error("--render takes a directory; do not also pass a posting id")
    if not args.render and not args.posting_id:
        parser.error("pass a posting id to generate, or --render DIR")
    if args.render:
        return _render(args.render)

    try:
        load_env()
    except EnvFileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        return _generate(args, client)
    except GenerateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except WriteError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _generate(args, client) -> int:
    store = Store(args.db)
    store.init_schema()

    posting = store.get(args.posting_id)
    if posting is None:
        raise GenerateError(f"no posting with id {args.posting_id!r}")
    verdict = store.role_verdict_of(args.posting_id)
    if verdict is None:
        raise GenerateError(
            f"{args.posting_id} has no role verdict - run "
            "`python -m jobhunt.matching --two-axis` first. Generation reuses "
            "the verdict and will not guess from the posting text.")

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    employers = {r["company"] for r in profile["roles"]}
    studies = load_case_studies(known_employers=employers)
    lens = lens_for(verdict.discipline)

    # The directory name carries BOTH company and role: one company can have
    # five open design roles, and collapsing them would overwrite an
    # application that had already been edited.
    out_dir = Path(args.out) / f"{slug(posting.company)}-{slug(verdict.role_title)}"
    existing = [out_dir / f for f in FILES if (out_dir / f).is_file()]
    if existing and not args.force:
        raise GenerateError(
            f"{out_dir} already exists with {len(existing)} file(s). Your edits "
            "are the approval, so this will not overwrite them. Re-run with "
            "--force to discard them.")

    roles = renderable_roles(profile)
    eligible_by_role = {r["id"]: eligible_bullets(r, lens) for r in roles}
    eligible_by_id = {b["id"]: b["text"]
                      for bs in eligible_by_role.values() for b in bs}
    if not eligible_by_id:
        raise GenerateError(
            f"no eligible bullets under the {lens!r} lens - every bullet is "
            "untagged or unapproved, so there is nothing to build a resume from")

    writer = Writer(_build_client(client),
                    **({"model": args.model} if args.model else {}))
    system = build_system(profile, lens)

    flat = [b for bs in eligible_by_role.values() for b in bs]
    selection = writer.select_bullets(
        system, build_bullet_user(posting.raw_text, verdict.model_dump(), flat))
    accepted = accept_selection(selection, eligible_by_id)
    if not accepted:
        raise GenerateError(
            "the model selected no usable bullets - every id it returned was "
            "outside the eligible set")

    by_role: dict[str, list] = {}
    for role in roles:
        ids = {b["id"] for b in eligible_by_role[role["id"]]}
        picked = [a for a in accepted if a.bullet_id in ids]
        if picked:
            by_role[role["id"]] = picked

    employers_used = {r["company"] for r in roles if r["id"] in by_role}
    citable = relevant_case_studies(studies, lens, employers_used)
    letter = writer.write_cover_letter(
        system, build_letter_user(posting.raw_text, posting.company,
                                  verdict.role_title, accepted, citable))
    invented = check_citations(letter, {c.title for c in citable})
    if invented:
        print(f"warning: dropped invented citation(s): {', '.join(invented)}",
              file=sys.stderr)
        letter = letter.model_copy(update={
            "cited_case_studies": [t for t in letter.cited_case_studies
                                   if t not in invented]})

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "resume.md").write_text(
        resume_markdown(profile, roles, by_role, lens), encoding="utf-8")
    (out_dir / "cover-letter.md").write_text(
        cover_letter_markdown(profile["identity"], posting.company,
                              verdict.role_title, letter, citable),
        encoding="utf-8")
    (out_dir / "posting.md").write_text(
        posting_markdown(posting.model_dump(), verdict.model_dump()),
        encoding="utf-8")

    refused = [a for a in accepted if a.refused_reason]
    print(f"wrote {out_dir}")
    print(f"  bullets: {len(accepted)} used, {len(refused)} re-angle(s) refused")
    for a in refused:
        print(f"    [{a.bullet_id}] {a.refused_reason}")
    return 0


def _render(directory: Path) -> int:
    """Turn the EDITED markdown into deliverables.

    Reads the files on disk rather than regenerating, so the output always
    matches what Sam actually approved.
    """
    from ..generation.deliver import (application_json, document_title,
                                      markdown_to_html, markdown_to_text)
    directory = Path(directory)
    resume_md = directory / "resume.md"
    if not resume_md.is_file():
        raise GenerateError(f"no resume.md in {directory}")
    posting_md = directory / "posting.md"
    resume = resume_md.read_text(encoding="utf-8")
    posting = posting_md.read_text(encoding="utf-8") if posting_md.is_file() else ""

    # The <title> becomes Chrome's suggested PDF filename, so it is what a
    # recruiter sees on the attachment. Name from the resume's own H1,
    # company and role from the posting.
    payload = application_json(posting, resume)
    # Empty, not "Resume" - a missing H1 would otherwise title the document
    # "Resume - Resume - Company - Role". document_title drops empty segments.
    name = next((l.lstrip("# ").strip() for l in resume.splitlines()
                 if l.startswith("# ")), "")
    company, role = payload.get("company"), payload.get("role_title")

    (directory / "resume.txt").write_text(
        markdown_to_text(resume), encoding="utf-8")
    (directory / "resume.html").write_text(
        markdown_to_html(resume, title=document_title(
            "Resume", name, company, role)),
        encoding="utf-8")
    letter_md = directory / "cover-letter.md"
    if letter_md.is_file():
        letter = letter_md.read_text(encoding="utf-8")
        (directory / "cover-letter.txt").write_text(
            markdown_to_text(letter), encoding="utf-8")
        (directory / "cover-letter.html").write_text(
            markdown_to_html(letter, title=document_title(
                "Cover Letter", name, company, role)), encoding="utf-8")
    (directory / "application.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")
    print(f"rendered {directory}")
    print("  open resume.html and print to PDF for a PDF copy")
    return 0
