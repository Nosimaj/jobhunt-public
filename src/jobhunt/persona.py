"""Who you are, in your own words: `persona.yaml`.

A resume has one summary at most, and none of them say how you want to be
positioned. These are written by you, never by a model, and flow through
ingest into `profile.json` and from there into both matching prompts.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field

from . import config
from .models import Strict, Summaries


class Persona(Strict):
    #: `design` - you as a designer. `eng` - you as a builder.
    summaries: Summaries
    #: The career-positioning paragraph the role rubric is anchored on: what
    #: you win, what you lose and to whom.
    positioning: str = Field(min_length=1)


def load_persona(path: Path | None = None) -> Persona:
    path = Path(path) if path is not None else config.PERSONA_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"no persona at {path} - copy persona.example.yaml to persona.yaml "
            "and fill it in")
    return Persona(**yaml.safe_load(path.read_text(encoding="utf-8")))
