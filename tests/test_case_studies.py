"""The published evidence a cover letter may cite.

`evidences` joins to an employer name in profile.json. Absent and wrong are
different: absent is a fact about the work (the Figma plugin is independent),
wrong is a typo that would silently drop a citation.
"""

import pytest

from jobhunt.generation.case_studies import CaseStudy, load_case_studies

EMPLOYERS = {"Tidewater Corporation", "Fabrikam Mobile", "Spokewell Repair"}


def write(tmp_path, body):
    p = tmp_path / "case_studies.yaml"
    p.write_text(body, encoding="utf-8")
    return p


MINIMAL = """
case_studies:
  - title: A Thing
    url: https://example.com/a
    pitch: Did a thing.
    evidences: Tidewater Corporation
    lens: design
"""


def test_the_shipped_file_loads_and_every_join_resolves():
    """The real file, against the real profile. A broken join here means a
    cover letter silently loses a citation."""
    import json

    from jobhunt import config
    if not config.PROFILE_PATH.is_file():
        pytest.skip("run `python -m jobhunt.ingest` first")
    employers = {r["company"] for r in
                 json.loads(config.PROFILE_PATH.read_text())["roles"]}
    studies = load_case_studies(known_employers=employers)
    assert len(studies) >= 1


def test_a_minimal_entry_loads(tmp_path):
    s = load_case_studies(write(tmp_path, MINIMAL), known_employers=EMPLOYERS)
    assert s[0].title == "A Thing"
    assert s[0].detail_url is None


def test_evidences_may_be_absent_for_independent_work(tmp_path):
    body = MINIMAL.replace("    evidences: Tidewater Corporation\n", "")
    s = load_case_studies(write(tmp_path, body), known_employers=EMPLOYERS)
    assert s[0].evidences is None


def test_an_evidences_value_matching_no_employer_is_a_loud_failure(tmp_path):
    body = MINIMAL.replace("Tidewater Corporation", "Tidewater Rela")
    with pytest.raises(ValueError, match="Tidewater Rela"):
        load_case_studies(write(tmp_path, body), known_employers=EMPLOYERS)


def test_an_unknown_lens_is_rejected(tmp_path):
    body = MINIMAL.replace("lens: design", "lens: marketing")
    with pytest.raises(Exception):
        load_case_studies(write(tmp_path, body), known_employers=EMPLOYERS)


def test_an_unknown_key_is_rejected(tmp_path):
    body = MINIMAL + "    priority: high\n"
    with pytest.raises(Exception):
        load_case_studies(write(tmp_path, body), known_employers=EMPLOYERS)


def test_a_missing_file_is_loud(tmp_path):
    with pytest.raises(FileNotFoundError, match="case_studies.yaml"):
        load_case_studies(tmp_path / "case_studies.yaml")


def test_an_empty_pitch_is_rejected(tmp_path):
    """The pitch is the sentence that lands in a cover letter. An empty one
    would render as a blank citation."""
    body = MINIMAL.replace("pitch: Did a thing.", 'pitch: ""')
    with pytest.raises(Exception):
        load_case_studies(write(tmp_path, body), known_employers=EMPLOYERS)


def test_joins_are_not_checked_when_no_employers_are_supplied(tmp_path):
    """`--print-prompt`-style callers may not have a profile loaded."""
    body = MINIMAL.replace("Tidewater Corporation", "Anything At All")
    assert load_case_studies(write(tmp_path, body))[0].evidences == "Anything At All"
