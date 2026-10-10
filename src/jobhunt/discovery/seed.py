"""Hand-curated ATS boards.

The registry is otherwise fed only by harvesting tokens out of HN posts, which
structurally cannot reach a company that never posted there - which is every
Boston hospital, university and hardware firm. This is the manual door.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field

from .. import config
from ..models import Strict
from .ats_token import PROVIDER_ORDER
from ..normalize import normalize_company

SEED_PATH = config.REPO_ROOT / "seed_boards.yaml"


class SeedBoard(Strict):
    company: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    token: str = Field(min_length=1)


def load_seed(path: Path | None = None) -> list[SeedBoard]:
    path = Path(path) if path is not None else SEED_PATH
    if not path.is_file():
        raise FileNotFoundError(f"no seed file at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    boards = [SeedBoard(**b) for b in data.get("boards", [])]
    unknown = {b.provider for b in boards} - set(PROVIDER_ORDER)
    if unknown:
        # Loud, per CLAUDE.md rule 5. A typo'd provider would otherwise be
        # registered and then fail every poll forever as a "dead token".
        raise ValueError(f"unknown ATS provider(s) in {path}: {sorted(unknown)}")
    return boards


def seed_boards(store, boards: list[SeedBoard]) -> int:
    """Register each board. Idempotent: re-running re-writes the same token."""
    for b in boards:
        store.set_ats_token(normalize_company(b.company), b.company,
                            b.provider, b.token)
    return len(boards)
