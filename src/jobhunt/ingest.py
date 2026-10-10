from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import config
from .overrides import load_overrides
from .parse_linkedin_pdf import extract_text, normalize_lines, parse_text
import pydantic
import yaml

from .env import EnvFileError, load_env
from .parse_resume import ResumeError, parse_file as parse_resume_file
from .persona import load_persona
from .reconcile import reconcile
from .review import render_review
from .snapshot import snapshot

_REGRESSION_THRESHOLD = 0.80


def _previous_role_count(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return len(json.loads(path.read_text(encoding="utf-8")).get("roles", []))
    except (json.JSONDecodeError, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobhunt.ingest",
                                     description="Build profile.json from resume sources.")
    parser.add_argument("--out", type=Path, default=config.DATA_DIR,
                        help="output directory (default: ./data)")
    args = parser.parse_args(argv)

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()

    try:
        return _ingest(out_dir, stamp)
    except (ResumeError, EnvFileError, FileNotFoundError, ValueError,
            pydantic.ValidationError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _ingest(out_dir: Path, stamp: str) -> int:
    # Persona and credentials first: both are cheap to check, and failing on
    # them after the PDF parse would waste the work before it.
    persona = load_persona()
    load_env()                                  # the resume is structured by Claude
    sources = config.sources()
    to_snapshot = {**sources}
    for role, path in config.ARCHIVAL_ONLY.items():
        if path.is_file():                      # archival only; absence is not an error
            to_snapshot[role] = path
    manifest = snapshot(to_snapshot, out_dir / "sources", today=stamp)

    pdf_text = extract_text(sources["pdf"])
    header_lines = normalize_lines(pdf_text)
    pdf_roles, pdf_conflicts = parse_text(pdf_text)
    resume = parse_resume_file(sources["resume"], persona.summaries)
    overrides = load_overrides(config.OVERRIDES_PATH)

    profile, report = reconcile(pdf_roles, resume, overrides, manifest.files,
                                pdf_header_lines=header_lines,
                                today=stamp, pdf_conflicts=pdf_conflicts)
    profile = profile.model_copy(update={"positioning": persona.positioning})

    profile_path = out_dir / "profile.json"
    previous = _previous_role_count(profile_path)
    if previous and len(profile.roles) < previous * _REGRESSION_THRESHOLD:
        keep = profile_path.with_suffix(".json.previous")
        keep.write_text(profile_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"WARNING: role count fell from {previous} to {len(profile.roles)}; "
              f"previous profile kept at {keep}", file=sys.stderr)

    profile_path.write_text(
        json.dumps(profile.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out_dir / "review.md").write_text(
        render_review(report, profile.generated_at, profile.complete), encoding="utf-8")

    print(f"wrote {profile_path} ({len(profile.roles)} roles, "
          f"complete={profile.complete})")
    print(f"wrote {out_dir / 'review.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
