from pathlib import Path

import pytest
from pydantic import ValidationError

from jobhunt.overrides import load_overrides

FIXTURE = Path(__file__).parent / "fixtures" / "overrides_synthetic.yaml"


def test_loads_aliases_manual_roles_and_engagements():
    ov = load_overrides(FIXTURE)
    assert ov.display_precision == "years"
    assert ov.aliases[0]["canonical"] == "Fernway Solutions"
    assert len(ov.manual_roles) == 1
    role = ov.manual_roles[0]
    assert role.company == "Northwind Traders"
    assert role.render == "feature"
    assert role.start == "2023-05" and role.end == "2024-04"
    assert [b.user_approved for b in role.bullets] == [True, False]
    assert len(ov.independent_engagements) == 1
    assert ov.independent_engagements[0].dates_confirmed is False


def test_rejects_unknown_render_value(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "manual_roles:\n"
        "  - company: X\n"
        "    title_of_record: T\n"
        "    title_presented: T\n"
        "    start: 2020-01\n"
        "    end: 2020-02\n"
        "    render: invisible\n",
        encoding="utf-8")
    with pytest.raises(ValidationError):
        load_overrides(bad)


def test_missing_file_yields_empty_overrides(tmp_path):
    ov = load_overrides(tmp_path / "absent.yaml")
    assert ov.manual_roles == []
    assert ov.aliases == []
    assert ov.display_precision == "years"


def test_yaml_dates_are_coerced_to_strings(tmp_path):
    # PyYAML parses an unquoted 2023-05-01 as datetime.date; YYYY-MM stays a string.
    f = tmp_path / "d.yaml"
    f.write_text(
        "manual_roles:\n"
        "  - company: X\n"
        "    title_of_record: T\n"
        "    title_presented: T\n"
        "    start: 2020-01\n"
        "    end: 2020-02\n",
        encoding="utf-8")
    ov = load_overrides(f)
    assert ov.manual_roles[0].start == "2020-01"
