from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "sources"
PROFILE_PATH = DATA_DIR / "profile.json"
REVIEW_PATH = DATA_DIR / "review.md"
JOBS_DB = DATA_DIR / "jobs.sqlite"
HN_SNAPSHOT_DIR = SNAPSHOT_DIR / "hn"
ATS_SNAPSHOT_DIR = SNAPSHOT_DIR / "ats"
OVERRIDES_PATH = REPO_ROOT / "overrides.yaml"

# Untracked credentials file. Gitignored; holds ANTHROPIC_API_KEY. Override
# with the JOBHUNT_ENV_FILE environment variable to keep it outside the repo.
ENV_PATH = REPO_ROOT / ".env"

# Steering knobs for the fit rubrics (target domains, seniority,
# disqualifiers). Tracked - it says what you want, not what happened to you.
SEARCH_CONFIG_PATH = REPO_ROOT / "search.yaml"

# Published evidence a cover letter may cite. Tracked - it is all public.
CASE_STUDIES_PATH = REPO_ROOT / "case_studies.yaml"

# Generated applications. Gitignored with the rest of data/ - they contain their
# full history and are derived, not source.
APPLICATIONS_DIR = DATA_DIR / "applications"

EVAL_LABELS = REPO_ROOT / "tests" / "fixtures" / "matching" / "eval_labels.yaml"

# Your two resume sources. Drop them in ./inputs/ or point at them from the
# environment:
#   JOBHUNT_LINKEDIN_PDF=/path/to/Profile.pdf
#   JOBHUNT_RESUME=/path/to/resume.docx
INPUTS_DIR = REPO_ROOT / "inputs"
RESUME_SUFFIXES = (".pdf", ".docx", ".txt", ".md")


def _default_resume() -> Path:
    for suffix in RESUME_SUFFIXES:
        candidate = INPUTS_DIR / f"resume{suffix}"
        if candidate.is_file():
            return candidate
    return INPUTS_DIR / "resume.docx"


# role -> absolute source path.
SOURCES: dict[str, Path] = {
    "pdf": Path(os.environ.get("JOBHUNT_LINKEDIN_PDF") or INPUTS_DIR / "linkedin.pdf"),
    "resume": Path(os.environ.get("JOBHUNT_RESUME") or _default_resume()),
}

# Extra files snapshotted for the record but never parsed. Empty by default.
ARCHIVAL_ONLY: dict[str, Path] = {}

# Who you are: the two lens summaries and your positioning. Read at ingest.
PERSONA_PATH = REPO_ROOT / "persona.yaml"
