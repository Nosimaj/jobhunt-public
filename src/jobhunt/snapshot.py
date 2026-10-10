from __future__ import annotations

import hashlib
import shutil
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .models import SourceRef


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    files: list[SourceRef] = Field(default_factory=list)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(sources: dict[str, Path], dest_root: Path,
             today: str | None = None) -> SnapshotManifest:
    """Copy each source into dest_root/<date>/ and record its digest.

    `role` keys not in ("pdf", "resume") are copied for the archival record but
    omitted from the manifest, which only describes parsed sources.

    Validates all sources exist before copying any, ensuring no partial snapshots.
    """
    stamp = today or date.today().isoformat()
    dest = Path(dest_root) / stamp
    dest.mkdir(parents=True, exist_ok=True)

    # First pass: validate all sources exist before copying any
    for role, src in sources.items():
        src = Path(src)
        if not src.is_file():
            raise FileNotFoundError(f"source '{role}' not found: {src}")

    # Second pass: copy and hash
    files: list[SourceRef] = []
    for role, src in sources.items():
        src = Path(src)
        shutil.copy2(src, dest / src.name)
        if role in ("pdf", "resume"):
            files.append(SourceRef(role=role, path=str(src),
                                   sha256=_sha256(src), snapshot_date=stamp))
    return SnapshotManifest(date=stamp, files=files)
