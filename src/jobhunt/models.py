from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Audience = Literal["design", "eng", "both", "untagged"]
Render = Literal["feature", "minimal", "omit"]
SourceKind = Literal["resume", "pdf", "manual"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(Strict):
    source: SourceKind
    snapshot_date: str


class Bullet(Strict):
    id: str
    text: str
    audience: Audience
    user_approved: bool
    provenance: Provenance

    @model_validator(mode="after")
    def _no_unreviewed_pdf_prose_marked_both(self) -> "Bullet":
        """PDF bullets are unreviewed LinkedIn exporter prose; 'both' means

        reviewed and resume-ready, so a PDF-sourced bullet may never carry it.
        This is the structural guard behind the reconciler's convention —
        enforced here so every future bullet-construction path inherits it.
        """
        if self.provenance.source == "pdf" and self.audience == "both":
            raise ValueError(
                "PDF-sourced bullets are unreviewed and must never be marked "
                "audience='both'; use 'untagged' until a human reviews and tags it"
            )
        return self


class Role(Strict):
    id: str
    company: str
    title_of_record: str
    title_presented: str
    start: str                      # YYYY-MM
    end: str                        # YYYY-MM or "present"
    location: str | None = None
    render: Render = "feature"
    do_not_include: str | None = None
    talking_points: str | None = None
    disclosure_rule: str | None = None
    bullets: list[Bullet] = Field(default_factory=list)


class Skill(Strict):
    text: str
    audience: Literal["design", "eng"]


class Identity(Strict):
    name: str
    headline: str
    location: str
    email: str
    links: dict[str, str] = Field(default_factory=dict)


class Summaries(Strict):
    design: str
    eng: str


class Project(Strict):
    title: str
    detail: str


class IndependentEngagement(Strict):
    id: str
    client: str
    work: str
    start: str
    end: str
    paid: bool
    dates_confirmed: bool
    supplement_only: bool = True


class ChronologyGap(Strict):
    after_role_id: str
    before_role_id: str
    months: int


class Gap(Strict):
    kind: str
    detail: str
    blocking: bool


class SourceRef(Strict):
    role: Literal["pdf", "resume"]
    path: str
    sha256: str
    snapshot_date: str


class Profile(Strict):
    schema_version: int = 1
    generated_at: str
    complete: bool
    sources: list[SourceRef]
    identity: Identity
    summaries: Summaries
    skills: list[Skill]
    roles: list[Role]
    projects: list[Project] = Field(default_factory=list)
    display_precision: Literal["years", "months"] = "years"
    independent_engagements: list[IndependentEngagement] = Field(default_factory=list)
    chronology_gaps: list[ChronologyGap] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    #: From persona.yaml. Rendered into both matching prompts.
    positioning: str = ""


# ---- intermediate parse results (not serialised to profile.json) ----

class PdfRole(Strict):
    company: str
    title: str
    start: str
    end: str
    duration_text: str
    location: str | None = None
    bullets: list[str] = Field(default_factory=list)


class ResumeBullet(Strict):
    text: str
    audience: Audience


class ResumeJob(Strict):
    org: str
    title: str
    date_placeholder: bool
    bullets: list[ResumeBullet] = Field(default_factory=list)


class ResumeProfile(Strict):
    summaries: Summaries
    skills: list[Skill] = Field(default_factory=list)
    jobs: list[ResumeJob] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)


class ReviewReport(Strict):
    """The review worklist.

    `blocking_gaps` means what its name says: everything appended to it is by
    definition blocking, and its presence alone sets `complete: false`.
    Consumers must NOT re-filter it on `Gap.blocking` -- doing so made the
    field name and its contents two competing sources of truth. The validator
    below keeps the contract honest, since the field currently has no
    producers and a future one could otherwise quietly append a non-blocking
    gap that no consumer would act on.
    """

    blocking_gaps: list[Gap] = Field(default_factory=list)
    chronology_gaps: list[ChronologyGap] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    tagging_worklist: list[str] = Field(default_factory=list)
    render_directives: list[str] = Field(default_factory=list)
    unapproved_bullets: list[str] = Field(default_factory=list)
    unconfirmed_dates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _blocking_gaps_are_blocking(self) -> "ReviewReport":
        offenders = [g.kind for g in self.blocking_gaps if not g.blocking]
        if offenders:
            raise ValueError(
                "blocking_gaps may only contain gaps with blocking=True; "
                f"got non-blocking {offenders}. A gap that does not block "
                "belongs in Profile.gaps, not here")
        return self
