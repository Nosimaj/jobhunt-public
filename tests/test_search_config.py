"""The steering knobs the two rubrics interpolate.

Kept in a file rather than in the prompt text so that changing what he is
looking for is an edit to data, not to a prompt - and so the same values reach
both rubrics without being restated in two places and drifting apart.
"""

import pytest

from jobhunt.matching.search_config import SearchConfig, load_search_config


def test_the_shipped_config_loads():
    cfg = load_search_config()
    assert cfg.seniority_range
    assert cfg.target_domains
    assert cfg.stage_preference
    assert cfg.company_disqualifiers


def test_a_missing_file_is_loud(tmp_path):
    with pytest.raises(FileNotFoundError, match="search.yaml"):
        load_search_config(tmp_path / "search.yaml")


def test_unknown_keys_are_rejected(tmp_path):
    p = tmp_path / "search.yaml"
    p.write_text("seniority_range: senior\ntarget_domains: [x]\n"
                 "stage_preference: seed\ncompany_disqualifiers: [y]\n"
                 "favourite_colour: blue\n", encoding="utf-8")
    with pytest.raises(Exception, match="favourite_colour"):
        load_search_config(p)


def test_empty_target_domains_is_rejected():
    """An empty list would render as a blank bullet in the rubric and quietly
    turn domain_pull into a coin flip."""
    with pytest.raises(Exception, match="target_domains"):
        SearchConfig(seniority_range="senior", target_domains=[],
                     stage_preference="seed", company_disqualifiers=["x"])


def test_empty_disqualifiers_is_rejected():
    with pytest.raises(Exception, match="company_disqualifiers"):
        SearchConfig(seniority_range="senior", target_domains=["health"],
                     stage_preference="seed", company_disqualifiers=[])


# --- geography (slice 6) --------------------------------------------------

def test_the_shipped_config_carries_home_metros_and_countries():
    """Both are declared. `home_metros`/`home_states` are read by
    `geography.reachability` (a FILTER input) and by `--show-actions`
    (ranking), and are never rendered into either rubric. `allowed_countries`
    is read by no code - see
    `test_allowed_countries_is_read_by_no_code_and_says_so` below."""
    cfg = load_search_config()
    assert cfg.home_metros
    assert cfg.allowed_countries == ["United States"]


def test_geography_is_not_rendered_into_the_company_rubric():
    """Location is a ROLE property. The old config smuggled it into the
    COMPANY verdict as a disqualifier, which would have made rubric B reject
    Discord, PermitFlow and LatchBio - the companies carrying 14 of the 23
    design roles the ATS source surfaces."""
    from jobhunt.matching.rubrics import build_company_system
    text = build_company_system(load_search_config())
    assert "commute" not in text.casefold()
    assert "return-to-office" not in text.casefold()


def test_no_disqualifier_mentions_the_office():
    cfg = load_search_config()
    joined = " ".join(cfg.company_disqualifiers).casefold()
    assert "office" not in joined and "commut" not in joined


def test_empty_home_metros_is_rejected():
    """Nothing would be hidden - there is no gate - but `--show-actions`
    would silently stop ranking: no role could ever count as commutable, so
    a Boston on-site role would sort level with a San Francisco one."""
    with pytest.raises(Exception, match="home_metros"):
        SearchConfig(seniority_range="senior", target_domains=["health"],
                     stage_preference="seed", company_disqualifiers=["x"],
                     home_metros=[], allowed_countries=["United States"])


# --- doc truth: there is no company gate ----------------------------------
#
# Commit 897c013 removed the design in which an onsite role outside
# `home_metros` was "scored only when its company already rates target".
# The code went; three comments asserting it stayed, in the two files a
# reader consults FIRST to learn what these knobs do.

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SEARCH_YAML = _ROOT / "search.yaml"
_SEARCH_CONFIG_PY = _ROOT / "src" / "jobhunt" / "matching" / "search_config.py"


def test_no_comment_claims_a_scoring_gate():
    """A gate that does not exist is the most expensive kind of stale
    comment: the next reader designs around it. `home_metros` is a
    RANKING signal for --show-actions, nothing is ever hidden, and it is
    never rendered into a rubric."""
    for path in (_SEARCH_YAML, _SEARCH_CONFIG_PY):
        text = path.read_text(encoding="utf-8").casefold()
        assert "scoring gate" not in text, path
        assert "gated behind" not in text, path
        assert "scored only when" not in text, path


def test_allowed_countries_is_read_by_no_code_and_says_so():
    """`grep -rn allowed_countries src/` finds only the declaration. US
    geography is enforced by the hardcoded regexes in ats_filters. The field
    is kept as declared config, but it must not look live."""
    hits = [p for p in (_ROOT / "src").rglob("*.py")
            if "allowed_countries" in p.read_text(encoding="utf-8")
            and p != _SEARCH_CONFIG_PY]
    assert hits == [], f"allowed_countries is now read by {hits}; update the comment"

    declaration = _SEARCH_CONFIG_PY.read_text(encoding="utf-8").casefold()
    assert "not read by any code" in declaration
    assert "ats_filters" in declaration


def test_us_geography_is_actually_enforced_by_ats_filters():
    """The thing allowed_countries appears to do, done elsewhere."""
    from jobhunt.discovery.ats_filters import geo_signal_for
    assert geo_signal_for("London, UK") == "non_us"
    assert geo_signal_for("Boston, MA") == "us"
