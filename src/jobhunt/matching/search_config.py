"""Steering knobs for the two fit rubrics.

The amendment's rubrics carry `{{...}}` placeholders precisely so that what the user
is looking for stays out of the prompt text. Changing target domains or
disqualifiers is then an edit to data, reviewable on its own, and both rubrics
read the same values instead of restating them and drifting apart.

Tracked, unlike `overrides.yaml`: none of this is sensitive. It says what the user
wants, not what happened to them.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field, field_validator

from .. import config
from ..models import Strict


class SearchConfig(Strict):
    seniority_range: str = Field(min_length=1)
    target_domains: list[str]
    stage_preference: str = Field(min_length=1)
    company_disqualifiers: list[str]

    #: ROLE geography. Renamed from `commute_metros` on 2026-10-06 and its
    #: MEANING CHANGED: it was ranking-only, it is now also a filter input,
    #: read by `geography.reachability`. Still never rendered into a rubric.
    home_metros: list[str]

    #: The backstop for home-area towns not named in `home_metros`. Two-letter
    #: USPS codes; `geography` matches them CASE-SENSITIVELY.
    home_states: list[str]

    #: Rubric A. What makes a role RELEVANT to their record. Since 2026-10-06
    #: this is the gate for engineering-titled roles, replacing the hybrid
    #: floor - so a vague entry here admits nearly everything.
    role_domains: list[str]

    #: CURRENTLY UNUSED - declared and validated here, and
    #: not read by any code: `grep -rn allowed_countries src/` finds only
    #: this declaration.
    #: US geography is enforced by the hardcoded regexes in
    #: `discovery/ats_filters.py`, which refuse a non-US role before it is
    #: stored. Kept as the written record of the intended scope and as the
    #: hook if that enforcement is ever made data-driven; editing it changes
    #: nothing today.
    allowed_countries: list[str]

    @field_validator("target_domains", "company_disqualifiers",
                     "home_metros", "home_states", "role_domains",
                     "allowed_countries")
    @classmethod
    def _not_empty(cls, v: list[str], info) -> list[str]:
        # An empty list renders as a blank bullet in the rubric, turning the
        # signal it governs into a coin flip. Empty `home_metros` renders
        # nowhere, but it would silently flatten `--show-actions`: no role
        # could ever count as commutable, so a Boston on-site role would sort
        # level with a San Francisco one and the ranking would quietly stop
        # doing its job.
        if not v:
            raise ValueError(f"{info.field_name} must not be empty")
        return v

    def bullets(self, field: str) -> str:
        return "\n".join(f"  - {item}" for item in getattr(self, field))


def load_search_config(path: Path | None = None) -> SearchConfig:
    path = Path(path) if path is not None else config.SEARCH_CONFIG_PATH
    if not path.is_file():
        raise FileNotFoundError(f"no search config at {path} (expected search.yaml)")
    return SearchConfig(**yaml.safe_load(path.read_text(encoding="utf-8")))
