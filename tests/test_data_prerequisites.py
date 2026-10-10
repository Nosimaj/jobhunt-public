"""Unconditional guards, so the suite cannot report green on a fresh clone.

`data/` is gitignored, and both `test_matching_prompt.py` and
`test_matching_cli.py` carry a module-level `skipif` on `data/profile.json`.
On a fresh checkout that silently removes EVERY test of the prompt and every
end-to-end CLI test, eval gate included - the suite reports a pass having
never exercised the crux of slice 3.

Slice 1 defended against exactly this with an unconditional source check
(`test_ingest_end_to_end.py`). This is the slice-3 equivalent: it fails
loudly, with the command that fixes it.
"""
from pathlib import Path

PROFILE = Path("tests/fixtures/profile_golden.json")


def test_the_generated_profile_is_present():
    assert PROFILE.is_file(), (
        f"{PROFILE} is missing, so the prompt and CLI tests would SKIP rather "
        "than fail. Regenerate it with:\n"
        "    .venv/bin/python -m jobhunt.ingest")
