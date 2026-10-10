"""Generating an application from a browser click.

Phase 1's dashboard was read-only. This module is the one place that spends
money, and every guard lives here:

- POST only, enforced by the route. A GET that spends can be fired by a
  prefetcher, a link scanner or a reload, with nobody deciding anything.
- Once per role: a ready directory (`resume.html` + `cover-letter.html` both
  present) is opened, never regenerated.
- Concurrent clicks for the same role are serialised by an in-process lock
  (`lock_for`), held by the route for the duration of the request.
  **Deliberately not a sentinel on disk.** A directory-as-lock was reviewed
  and rejected (round 1, CRITICAL-2): a crash mid-generation would leave a
  directory that reads as "in progress" forever, wedging the role behind
  the CLI's own `--force` guard - trading a double-charge for a permanent
  dead end. An in-process lock has no such wedge mode: it clears itself the
  moment the process restarts, which is sufficient for a single-user
  localhost tool that is not expected to survive a crash mid-request anyway.

`run_generation` writes files. Neither it nor this module opens a database
connection of its own - `generate.cli.main` opens its *own* `Store`
(read-write, not `read_only=True`) to read the posting and verdict, and
calls the idempotent `init_schema()` first, but it never inserts or updates
a row. The dashboard's own connections, in `app.py`, stay read-only
throughout.
"""

from __future__ import annotations

import re
import io
import threading
from contextlib import redirect_stderr
from pathlib import Path

from .. import config
from ..generation.render import slug

APPLICATIONS_ROOT = config.APPLICATIONS_DIR

#: The only files a browser may fetch out of an application directory.
SERVABLE = frozenset({"resume.html", "cover-letter.html", "resume.txt",
                      "cover-letter.txt", "application.json"})

#: Both must exist for the application to count as finished.
_REQUIRED = ("resume.html", "cover-letter.html")

#: One lock per posting id, created on first use. Guards creation of the
#: dict entry itself, not the generation work - the per-posting lock below
#: does that.
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def application_dir(company: str, role_title: str | None) -> Path:
    """Must match what the generate CLI writes, or the 'already exists'
    guard never fires and the dashboard pays twice under a second name."""
    return APPLICATIONS_ROOT / f"{slug(company)}-{slug(role_title or 'role')}"


def application_state(directory: Path) -> str:
    directory = Path(directory)
    if all((directory / f).is_file() for f in _REQUIRED):
        return "ready"
    return "absent"


def safe_application_path(root: Path, dirname: str, filename: str) -> Path:
    """Resolve a requested document, refusing anything outside `root`.

    The directory name comes from a URL, so it is attacker-controlled in
    principle even on localhost.
    """
    if filename not in SERVABLE:
        raise ValueError(f"{filename!r} is not a servable document")
    root = Path(root).resolve()
    target = (root / dirname / filename).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{dirname!r} escapes the applications directory")
    return target


def lock_for(posting_id: str) -> threading.Lock:
    """The in-process concurrency guard for one posting id.

    The route acquires this non-blockingly before calling `run_generation`
    and releases it in a `finally`, so a second concurrent request for the
    same role sees it held and returns a "generating" state instead of
    spending again - see the module docstring for why this is a lock and
    not a directory.
    """
    with _LOCKS_GUARD:
        lock = _LOCKS.get(posting_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[posting_id] = lock
        return lock


#: The API's own explanation, inside the repr of its error body. A 400 is
#: usually something they can act on - "your credit balance is too low",
#: "prompt is too long" - and that sentence sat beyond the template's 60-char
#: cut, so a real failure read as mojibake.
_API_MESSAGE_RE = re.compile(r"['\"]message['\"]\s*:\s*['\"](.+?)['\"]\s*\}", re.S)
_STAGE_RE = re.compile(r"^([^:]+):\s")


def friendly_error(raw: str | None) -> str:
    """Turn a raw generation failure into one sentence they can act on.

    Keeps the stage label ("resume selection", "cover letter") because it says
    WHICH call failed, and replaces the serialised error body with the API's
    own message. Anything that is not an API error passes through untouched.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    m = _API_MESSAGE_RE.search(text)
    if not m:
        return text
    stage = _STAGE_RE.match(text)
    message = m.group(1).strip()
    return f"{stage.group(1)}: {message}" if stage else message


def run_generation(db_path: Path, posting_id: str, out_dir: Path,
                   client=None) -> None:
    """Write the draft, then render it into the documents the dashboard
    opens - both passes, because `generate.cli.main` called with a posting
    id writes only `resume.md`/`cover-letter.md`/`posting.md`; the HTML
    files `application_state` checks for come from the separate `--render`
    pass (CRITICAL-1, review round 1: skipping it left every generated role
    permanently wedged, "ready" with two 404ing links).

    `client` is injectable so tests can exercise this real code path with
    `tests/test_generate_cli.py`'s `FakeClient` instead of a mock of this
    whole function.

    On failure, raises with the CLI's OWN stderr message (IMPORTANT-3,
    review round 1) rather than a generic one - "no role verdict", "already
    exists" and "no eligible bullets" must not all read identically.
    """
    from ..generate.cli import main as generate_main

    out_dir = Path(out_dir)
    # A directory holding the markdown but no rendered HTML is HALF DONE, not
    # finished and not absent. Running the generate pass against it makes the
    # CLI refuse ("already exists"), and the render pass then never runs - so
    # the state was unreachable by any recovery available from the browser.
    # Completing it costs nothing and makes no API call.
    needs_only_render = (out_dir / "resume.md").is_file()
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        rc = 0
        if not needs_only_render:
            rc = generate_main([posting_id, "--db", str(db_path),
                                "--out", str(out_dir.parent)], client=client)
        if rc == 0:
            rc = generate_main(["--render", str(out_dir)])
    if rc != 0:
        reason = friendly_error(stderr.getvalue()) or (
            "generation failed - see the server log")
        raise RuntimeError(reason)
