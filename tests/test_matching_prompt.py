import json
import re
from pathlib import Path

import pytest
import yaml

from jobhunt.discovery.models import Posting
from jobhunt.matching.prompt import RUBRIC, build_system, build_user, prompt_sha

PROFILE = Path("tests/fixtures/profile_golden.json")
pytestmark = pytest.mark.skipif(
    not PROFILE.is_file(),
    reason="run `python -m jobhunt.ingest` first to generate data/profile.json")


@pytest.fixture(scope="module")
def profile():
    return json.loads(PROFILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def system(profile):
    return build_system(profile)


def test_both_lens_summaries_are_present(system, profile):
    assert profile["summaries"]["design"][:60] in system
    assert profile["summaries"]["eng"][:60] in system


def test_all_skills_present_and_lens_tagged(system, profile):
    for s in profile["skills"]:
        assert s["text"] in system
    assert "Fikasa, Firudon, Fedav2" in system


def test_unreviewed_bullets_are_marked_as_such(system):
    # untagged bullets are included because matching reads rather than
    # publishes - but the model must know which prose the user has not reviewed.
    assert "unreviewed" in system.lower()


def test_every_non_omitted_role_appears(system, profile):
    shown = [r for r in profile["roles"] if r.get("render") != "omit"]
    omitted = [r for r in profile["roles"] if r.get("render") == "omit"]
    assert shown, "fixture should contain roles to show"
    assert omitted, "fixture should contain at least one omitted role, or this test proves nothing"
    for r in shown:
        assert r["company"] in system


def test_the_rubric_is_present(system):
    for phrase in ("spans disciplines", "full-stack", "brand",
                   "would spend an hour"):
        assert phrase.lower() in system.lower()


def test_the_word_overlap_counter_example_is_present_and_invented(system):
    # The worked example of word-overlap-is-not-fit (spec 5.1). It used to
    # name Fastly, which IS one of the 25 labelled postings - during --eval
    # that hands over one row of the answer sheet. It is now invented, and
    # says so, so the model cannot mistake it for a real posting.
    assert "Worked example (invented" in system
    assert "distributed-systems engineer" in system
    assert "Fastly" not in system


def test_it_forbids_modelling_the_users_confidence(system):
    assert "confidence" in system.lower()


def test_hybrid_gate_is_stated_with_its_consequence(system):
    low = system.lower()
    assert "contextual" in low and "explicit" in low
    assert "maybe" in low


def test_omitted_roles_are_excluded(system, profile):
    # Sam Example LLC is render:omit - it must not be offered as evidence.
    omitted = [r["company"] for r in profile["roles"] if r["render"] == "omit"]
    assert omitted, "fixture should contain at least one omitted role"
    for c in omitted:
        assert c not in system


def test_user_block_is_only_the_posting_text():
    p = Posting(id="hn:1", source="hn", source_thread_id="9", company="Acme",
                company_normalised="acme", raw_text="Acme | Design Engineer | Remote US",
                apply_url=None, is_remote=True, geo_signal="us",
                posted_at="2026-09-01", first_seen="x", last_seen="x", status="new")
    u = build_user(p)
    assert "Acme | Design Engineer | Remote US" in u
    # No other posting, no prior verdict - the judgement must be absolute.
    assert "strong" not in u.lower() and "bucket" not in u.lower()


def test_prompt_sha_is_stable_and_content_sensitive(profile):
    a = prompt_sha(profile, "m")
    assert a == prompt_sha(json.loads(PROFILE.read_text(encoding="utf-8")), "m")
    changed = json.loads(json.dumps(profile))
    changed["skills"].append({"text": "Rust", "audience": "eng"})
    assert prompt_sha(changed, "m") != a


def test_prompt_sha_ignores_generated_at(profile):
    # A re-run of ingest must not invalidate every stored verdict.
    changed = json.loads(json.dumps(profile))
    changed["generated_at"] = "1999-01-01T00:00:00"
    assert prompt_sha(changed, "m") == prompt_sha(profile, "m")


def test_prompt_sha_changes_with_the_model(profile):
    # A --model swap must invalidate stored verdicts: otherwise `--eval
    # --model X` reports the PREVIOUS model's numbers, reusing every verdict.
    assert prompt_sha(profile, "claude-opus-5") != prompt_sha(profile, "claude-haiku-4-5")


def test_prompt_sha_changes_when_the_rubric_changes(profile, monkeypatch):
    """The whole reason for running --eval is to tune the rubric. Keyed on the
    profile alone, an edited rubric reused every stored verdict and reported a
    score for a prompt that no longer existed."""
    import jobhunt.matching.prompt as mod
    before = prompt_sha(profile, "m")
    monkeypatch.setattr(mod, "RUBRIC", RUBRIC + "\n\nAlso: prefer remote roles.")
    assert prompt_sha(profile, "m") != before


def test_the_eval_set_is_not_given_away_in_the_rubric():
    """The rubric must not name a labelled posting, nor state the label set's
    bucket distribution - during --eval that is the answer sheet, not
    calibration. Checked against RUBRIC, not the whole system prompt: the
    profile half is Sam's own history and a collision there is a
    coincidence, not a leak.
    """
    labels = yaml.safe_load(
        Path("tests/fixtures/matching/eval_labels.yaml").read_text(encoding="utf-8"))
    for row in labels["labels"]:
        company = row["company"].lstrip("*").split("(")[0].split("|")[0].strip()
        if len(company) < 6 or " " in company:
            continue          # too short or too generic to test meaningfully
        assert not re.search(rf"\b{re.escape(company)}\b", RUBRIC, re.I), (
            f"the rubric names {company}, which is in the eval set")
    assert "20 of them" not in RUBRIC and "4 `maybe`" not in RUBRIC


def test_the_no_list_overrides_a_contextual_signal(system):
    """The most likely severe inversion on this corpus. Nearly every HN poster
    is a startup, so a `contextual` hybrid signal is satisfiable for most of
    the board - and the rubric separately says engineering titles are `no`
    "even when the technology stack matches his exactly". With no stated
    precedence, "Founding Engineer" and "wants product-minded engineers" both
    read as hybrid, against 20 emphatic human `no`s.
    """
    low = system.lower()
    assert "a signal is not permission" in low
    assert "overrides a `contextual` signal" in low
    assert "founding engineer" in low
    assert "product-minded" in low


def test_fit_score_is_anchored_to_the_buckets(system):
    # An uninstructed 0-100 self-rating is the classic inflation vector.
    assert "`no` is 0-39" in system and "`strong` is 70-100" in system


def test_the_measured_size_of_the_cached_system_block(system):
    """Drift detector. The cacheable prefix minimum on Opus 5 is 512 tokens,
    so this must stay comfortably above it or the cache discount silently
    stops applying."""
    words = len(system.split())
    assert 2000 < words < 4000, f"system prompt is now {words} words"


def test_measured_profile_shape(profile):
    # Pinned, because iterating the profile to build assertions from it can
    # never fail on drift - the measured-facts trap in CLAUDE.md.
    assert len(profile["skills"]) == 15
    assert len(profile["roles"]) == 21
