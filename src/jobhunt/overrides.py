from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Audience, Render


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualBullet(Strict):
    text: str
    audience: Audience
    user_approved: bool = False

    @field_validator("text")
    @classmethod
    def _collapse(cls, v: str) -> str:
        return " ".join(v.split())


class ManualRole(Strict):
    company: str
    title_of_record: str
    title_presented: str
    start: str
    end: str
    location: str | None = None
    render: Render = "feature"
    bullets: list[ManualBullet] = Field(default_factory=list)
    do_not_include: str | None = None
    disclosure_rule: str | None = None
    talking_points: str | None = None
    references: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def _as_text(cls, v: Any) -> str:
        return str(v)

    @field_validator("do_not_include", "disclosure_rule", "talking_points", mode="after")
    @classmethod
    def _collapse_optional(cls, v: str | None) -> str | None:
        return " ".join(v.split()) if v else v


class RoleOverride(Strict):
    company: str
    start: str | None = None      # optional disambiguator when an employer repeats
    render: Render = "feature"
    reason: str | None = None

    @field_validator("start", mode="before")
    @classmethod
    def _as_text(cls, v: Any) -> str | None:
        return str(v) if v is not None else v

    @field_validator("reason", mode="after")
    @classmethod
    def _collapse_optional(cls, v: str | None) -> str | None:
        return " ".join(v.split()) if v else v


class ManualEngagement(Strict):
    client: str
    work: str
    start: str
    end: str
    paid: bool = True
    dates_confirmed: bool = False

    @field_validator("start", "end", mode="before")
    @classmethod
    def _as_text(cls, v: Any) -> str:
        return str(v)


class Overrides(Strict):
    aliases: list[dict] = Field(default_factory=list)
    manual_roles: list[ManualRole] = Field(default_factory=list)
    role_overrides: list[RoleOverride] = Field(default_factory=list)
    independent_engagements: list[ManualEngagement] = Field(default_factory=list)
    display_precision: Literal["years", "months"] = "years"


def load_overrides(path: Path) -> Overrides:
    """Load overrides. A missing file is not an error - it means no overrides."""
    path = Path(path)
    if not path.is_file():
        return Overrides()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Overrides.model_validate(raw)
