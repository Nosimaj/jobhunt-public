"""`python -m jobhunt.dashboard` - localhost only, by design.

The database holds their full work history, their email and a employment-history
narrative. Binding anything but 127.0.0.1 would need an auth decision and a
TLS decision to gain nothing while they are sitting at the machine.
"""

import argparse
from pathlib import Path

import uvicorn

from .. import config
from .app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobhunt.dashboard")
    parser.add_argument("--db", type=Path, default=config.JOBS_DB)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    print(f"http://127.0.0.1:{args.port}")
    uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port,
                log_level="warning")
    return 0


raise SystemExit(main())
