"""Load the published evidence a cover letter may cite.

Hand-written rather than parsed: the portfolio's case-study markup has no
per-study anchors, and the pitch is positioning Sam owns rather than text
a parser extracts. See spec section 2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from .. import config
from ..models import Strict

Lens = Literal["design", "eng", "both"]


class CaseStudy(Strict):
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    pitch: str = Field(min_length=1)
    lens: Lens
    #: Employer exactly as it appears in profile.json, or None for
    #: independent work. Absent and wrong are different - absent is a fact
    #: about the work, wrong is a typo that would drop a citation silently.
    evidences: str | None = None
    #: Optional longer write-up, e.g. one that carries more figures.
    detail_url: str | None = None

    @property
    def text(self) -> str:
        return " ".join(self.pitch.split())


def load_case_studies(path: Path | None = None,
                      known_employers: set[str] | None = None
                      ) -> list[CaseStudy]:
    """Load and validate. Raises on a join that cannot resolve.

    `known_employers` is optional so a caller with no profile loaded can still
    read the file; when supplied, every non-null `evidences` must match.
    """
    path = Path(path) if path is not None else config.CASE_STUDIES_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"no case studies at {path} (expected case_studies.yaml)")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    studies = [CaseStudy(**entry) for entry in (raw.get("case_studies") or [])]
    if known_employers is not None:
        for study in studies:
            if study.evidences and study.evidences not in known_employers:
                raise ValueError(
                    f"case study {study.title!r} evidences "
                    f"{study.evidences!r}, which matches no employer in the "
                    "profile - a typo here silently drops the citation")
    return studies
