"""What the model is allowed to return."""

from __future__ import annotations

from pydantic import Field

from ..models import Strict


class ChosenBullet(Strict):
    """One selected bullet, optionally re-angled."""
    bullet_id: str = Field(
        min_length=1,
        description="The id of a bullet from the ELIGIBLE list. You may not "
                    "invent an id and you may not use one that is absent "
                    "from that list.")
    rewritten: str = Field(
        min_length=1,
        description="The bullet, optionally re-angled to foreground what this "
                    "posting asks for. Keep their wording. You may NOT add a "
                    "number, a company name or any fact absent from the "
                    "original - a re-angle that introduces one is discarded "
                    "and the original used instead.")
    answers: str = Field(
        min_length=1,
        description="Which requirement of the posting this bullet answers, in "
                    "a few words.")


class ResumeSelection(Strict):
    bullets: list[ChosenBullet] = Field(
        description="The selected bullets, in the order they should appear.")


class CoverLetter(Strict):
    paragraphs: list[str] = Field(
        min_length=1,
        description="The body. ONE paragraph as a single list item - not "
                    "several. No salutation and no sign-off; those are added "
                    "when the document is rendered.")
    cited_case_studies: list[str] = Field(
        default_factory=list,
        description="Titles of case studies referenced, copied EXACTLY from "
                    "the list supplied. Cite only what you were given.")


class AcceptedBullet(Strict):
    """A bullet after the guards have run."""
    bullet_id: str
    text: str
    #: None when the re-angle was accepted; otherwise why it was refused and
    #: the original used instead.
    refused_reason: str | None = None
