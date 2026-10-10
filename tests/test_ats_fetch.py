"""Fetching one company's board. The fetcher is injected; no live calls."""

import json
from pathlib import Path

import pytest

from jobhunt.discovery.ats import BOARD_URL, BoardError, fetch_board

FIX = Path(__file__).parent / "fixtures" / "ats"


def fake(payload, seen=None):
    def _fetch(url):
        if seen is not None:
            seen.append(url)
        if isinstance(payload, Exception):
            raise payload
        return payload
    return _fetch


def test_greenhouse_requests_content():
    """Without ?content=true the list endpoint returns titles and no body,
    and rubric A cannot apply the Hybrid Test to a title."""
    seen = []
    payload = json.loads((FIX / "greenhouse-wikimedia.json").read_text())
    fetch_board("greenhouse", "wikimedia", "Wikimedia", fetch=fake(payload, seen))
    assert "content=true" in seen[0]


def test_each_provider_has_a_url_template():
    assert set(BOARD_URL) == {"greenhouse", "ashby", "lever", "workable",
                           "teamtailor"}
    for template in BOARD_URL.values():
        assert "{token}" in template


def test_roles_come_back_parsed():
    payload = json.loads((FIX / "ashby-runway.json").read_text())
    roles = fetch_board("ashby", "runway-ml", "Runway", fetch=fake(payload))
    assert roles and roles[0].provider == "ashby"


def test_a_transport_failure_raises_board_error():
    """A dead token is one company's problem. It must be catchable per
    company so the other 41 boards still run."""
    with pytest.raises(BoardError, match="runway-ml"):
        fetch_board("ashby", "runway-ml", "Runway",
                    fetch=fake(RuntimeError("HTTP 404")))


def test_a_non_dict_payload_raises_board_error_not_a_type_error():
    with pytest.raises(BoardError, match="expected JSON, got str"):
        fetch_board("ashby", "t", "Co", fetch=fake("<html>404</html>"))


def test_an_unknown_provider_raises_board_error_naming_the_company():
    """The guard fires before any fetch - no fetcher is even provided."""
    with pytest.raises(BoardError, match="Co"):
        fetch_board("bamboohr", "t", "Co")


def test_a_payload_the_real_parser_chokes_on_raises_board_error():
    """A dict-or-list payload passes the isinstance guard but can still be
    the wrong shape for its provider - here an Ashby board answering with a
    bare JSON list instead of {"jobs": [...]}. `_ashby` calls `.get` on it
    and a real AttributeError from ats_parse.parse must still come back as
    a BoardError naming the company and token, not escape raw."""
    with pytest.raises(BoardError, match="Co.*t"):
        fetch_board("ashby", "t", "Co", fetch=fake([]))


def test_the_raw_response_is_snapshotted(tmp_path):
    """Same discipline as hn.py: keep the raw response so a parse change can
    be re-run against what the board actually said."""
    payload = json.loads((FIX / "ashby-runway.json").read_text())
    fetch_board("ashby", "runway-ml", "Runway", fetch=fake(payload),
                snapshot_dir=tmp_path)
    written = list(tmp_path.glob("ashby-runway-ml-*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["jobs"]
