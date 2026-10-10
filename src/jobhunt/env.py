"""Load credentials from an untracked `.env`.

The `anthropic` SDK resolves `ANTHROPIC_API_KEY` from the process environment
and nowhere else, and a `launchd` job runs with a minimal environment that
never sources `~/.zshrc` - so a shell-profile export cannot serve a scheduled
run. This module is the one place a key reaches the environment.

Pure parsing (`parse_env_text`) is split from I/O (`load_env`) so the format
is testable without touching disk, per the project convention.

`.env` is gitignored and holds a real secret. Nothing here ever prints a
value: the permission warning names the path only, and `--status` reports the
SOURCE of a credential, never the credential.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import MutableMapping

from . import config

#: The only credential this project needs.
KEY = "ANTHROPIC_API_KEY"

#: Points the loader at a file outside the repo - a shared secrets directory,
#: or a copy on another machine. Set it and the repo `.env` is not consulted.
ENV_FILE_VAR = "JOBHUNT_ENV_FILE"


class EnvFileError(RuntimeError):
    """The env file is missing or malformed.

    Loud by design (CLAUDE.md rule 5). The silent alternative - treat an
    unparseable secrets file as "no credentials" - reports a typo as an
    authentication failure, which is a much longer walk to the real cause.
    """


def parse_env_text(text: str) -> dict[str, str]:
    """Parse env-file text into a mapping. Pure.

    Deliberately small: comments, blank lines, an optional `export` prefix and
    optional matching quotes. No interpolation and no multi-line values - this
    file holds one secret, and every extra feature is a way to silently read a
    different value than the one on the page.
    """
    values: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            raise EnvFileError(
                f"line {lineno}: expected NAME=value, got {line.split('=')[0]!r}")
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            raise EnvFileError(f"line {lineno}: empty variable name")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def _warn_if_readable_by_others(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        # A warning, not a refusal: this file is meant to be copied between
        # machines, and a strange umask on the far end should not stop a run.
        print(f"warning: {path} has insecure permissions - mode {mode:03o} "
              f"is readable by other users; run `chmod 600 {path}`",
              file=sys.stderr)


def load_env(environ: MutableMapping[str, str] | None = None,
             repo_env: Path | None = None) -> str:
    """Resolve credentials into `environ` and report where they came from.

    Order, and nothing else:

    1. `ANTHROPIC_API_KEY` already set (and non-empty) - used as-is. An
       already-set variable is NEVER overwritten, so a one-off
       `ANTHROPIC_API_KEY=... python -m jobhunt.matching` still wins and the
       test suite is unaffected by whatever is on the developer's disk.
    2. `JOBHUNT_ENV_FILE`, if set. Missing file raises: a path you pointed at
       and that is not there is a misconfiguration, and falling back to the
       repo `.env` would quietly use a different key than you asked for.
    3. `<repo root>/.env`.
    4. Nothing. Silent - `--print-prompt`, `--show` and `--status` are
       required to work with no credentials at all, and the `AuthError` in
       `match.py` already explains the failure at the point it matters.

    Returns `"environment"`, the path loaded from, or `"none"`. The return
    value describes where `ANTHROPIC_API_KEY` came from, so a file that loads
    successfully but does not define it reports `"none"`.
    """
    if environ is None:
        environ = os.environ
    if environ.get(KEY):
        return "environment"

    explicit = environ.get(ENV_FILE_VAR)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise EnvFileError(
                f"{ENV_FILE_VAR} points at {path}, which does not exist")
    else:
        path = Path(repo_env) if repo_env is not None else config.ENV_PATH
        if not path.is_file():
            return "none"

    _warn_if_readable_by_others(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EnvFileError(f"cannot read {path}: {exc}") from exc

    for name, value in parse_env_text(text).items():
        # `setdefault` is the no-override rule: the real environment always
        # beats the file, for every variable, not just the key.
        if not environ.get(name):
            environ[name] = value

    return str(path) if environ.get(KEY) else "none"
