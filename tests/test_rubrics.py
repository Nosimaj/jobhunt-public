"""The two rubric prompts. Pure text, so testable with no API key.

The property that matters most is SEPARATION: the role rubric must not invite
company reasoning and the company rubric must not invite role reasoning. The
first eval failed because one prompt was asked to hold both, and company
appeal leaked into a role verdict.
"""

import pytest

from jobhunt.matching.rubrics import (build_company_system, build_company_user,
                                      build_role_system, build_role_user,
                                      company_prompt_sha, role_prompt_sha)
from jobhunt.matching.search_config import load_search_config

CFG = load_search_config()


def flat(text):
    """Collapse whitespace: the rubrics are hard-wrapped, so a phrase check
    must not depend on where a line happens to break."""
    return " ".join(text.split()).casefold()


@pytest.fixture
def profile():
    import json
    from jobhunt import config
    if not config.PROFILE_PATH.is_file():
        pytest.skip("run `python -m jobhunt.ingest` first")
    return json.loads(config.PROFILE_PATH.read_text())


def test_no_placeholder_survives_rendering(profile):
    for text in (build_role_system(profile, CFG), build_company_system(CFG)):
        assert "{{" not in text and "}}" not in text


def test_the_role_rubric_states_that_titles_are_not_evidence(profile):
    assert "titles are not evidence" in flat(build_role_system(profile, CFG))


def test_the_role_rubric_names_stack_overlap_as_insufficient(profile):
    """The exact failure the first eval produced: Biobase was scored `strong`
    because its Python/FastAPI/agents stack resembled something he built."""
    text = flat(build_role_system(profile, CFG))
    assert "resemblance" in text or "stack" in text


def test_the_role_rubric_does_not_ask_about_the_company(profile):
    """Company appeal must have no route into the role verdict."""
    text = flat(build_role_system(profile, CFG))
    for leak in ("company_fit", "domain pull", "would he want a conversation",
                 "outreach"):
        assert leak not in text


def test_geography_is_not_rendered_into_the_role_rubric(profile):
    """Location is a ROLE property, but rubric A must never learn WHERE HE
    LIVES. If it were told, it would score a San Francisco design role `no`
    on "a relocation he is excluded from" - suppressing the role exactly as
    a company gate would, only more quietly. See tests/test_commute.py's
    module docstring - `home_metros`/`home_states` are read in code - by
    `geography.reachability` as a FILTER input and by `--show-actions` for
    ranking - and are never a prompt input.

    Mirrors `test_geography_is_not_rendered_into_the_company_rubric` in
    tests/test_search_config.py. The rubric text legitimately contains the
    generic phrase "a relocation he is excluded from" as one example of a
    hard requirement (an employer demanding relocation with no remote/hybrid
    option) - that names no place and is fine. What must never appear is one
    of his configured commute metros, or the word "commute" itself, either of
    which would mean his home city reached the model.
    """
    text = build_role_system(profile, CFG).casefold()
    for metro in CFG.home_metros:
        assert metro.casefold() not in text, (
            f"{metro!r} (a commute metro) leaked into the role rubric")
    assert "commute" not in text


def test_the_company_rubric_ignores_the_openings():
    assert "ignore what roles are currently posted" in flat(
        build_company_system(CFG))


def test_the_company_rubric_does_not_mention_role_fit():
    text = flat(build_company_system(CFG))
    for leak in ("role_fit", "hybrid test", "seniority"):
        assert leak not in text


def test_neither_rubric_reveals_the_action_matrix(profile):
    """A model that knows which action its verdict produces can reason
    backwards into the verdict that produces the action it prefers."""
    for text in (build_role_system(profile, CFG), build_company_system(CFG)):
        for action in ("apply_priority", "outreach_lead", "maybe_queue",
                       "apply_if_comp"):
            assert action not in text


def test_the_company_rubric_carries_the_configured_domains():
    text = build_company_system(CFG)
    for domain in CFG.target_domains:
        assert domain in text


def test_the_company_rubric_carries_the_configured_disqualifiers():
    text = build_company_system(CFG)
    for dq in CFG.company_disqualifiers:
        assert dq in text


def test_the_role_rubric_carries_the_configured_seniority():
    assert CFG.seniority_range in build_role_system({"summaries": {"design": "d",
        "eng": "e"}, "skills": [], "projects": [], "roles": []}, CFG)


def test_role_sha_changes_when_the_config_changes(profile):
    other = CFG.model_copy(update={"seniority_range": "junior"})
    assert role_prompt_sha(profile, CFG, "m") != role_prompt_sha(profile, other, "m")


def test_company_sha_changes_when_the_domains_change():
    other = CFG.model_copy(update={"target_domains": ["something else"]})
    assert company_prompt_sha(CFG, "m") != company_prompt_sha(other, "m")


def test_company_sha_is_independent_of_the_profile():
    """Company fit does not depend on the resume, so re-running ingest must
    not invalidate a cached company verdict."""
    assert company_prompt_sha(CFG, "m") == company_prompt_sha(CFG, "m")


def test_the_user_blocks_carry_only_their_own_subject():
    class P:
        raw_text = "Acme | Senior Design Engineer | Remote"
        company = "Acme"
    assert "Acme | Senior" in build_role_user(P())
    company_block = build_company_user("Acme", "Acme builds bike computers.")
    assert "bike computers" in company_block and "Acme" in company_block


def test_role_rendering_is_not_duplicated():
    """`_render_roles` existed verbatim in both prompt.py and rubrics.py. Two
    copies means a fix to the `[unreviewed]` marker rule, or to render:omit,
    silently applies to only one path."""
    from jobhunt.matching import prompt, rubrics
    assert prompt._render_roles is rubrics._render_roles


def test_the_role_rubric_renders_role_domains(profile):
    text = flat(build_role_system(profile, CFG))
    for d in CFG.role_domains:
        assert flat(d) in text
    assert "telehealth and care delivery" in text


def test_the_role_rubric_states_the_either_or_floor_in_prose(profile):
    """A validator only rejects, and a rejection costs the whole paid verdict,
    so the floor must be stated in the prompt as well as enforced."""
    text = flat(build_role_system(profile, CFG))
    assert "hybrid pass" in text and "domain match" in text
    assert "does not apply to `design` or `design_engineering`" in text
    assert "`weak` hybrid still counts" in text


def test_the_role_rubric_states_na_and_adjacent_rules(profile):
    text = flat(build_role_system(profile, CFG))
    assert "`n/a` is only for non-engineering roles" in text
    assert "`adjacent` both require `domain_evidence`" in text
    assert "adjacent`-only match" in text


def test_the_three_statements_of_the_gate_agree(profile):
    """Floor prose, `# Verdicts` block and the `role_fit` description must all
    say: strong needs a hybrid pass or a DIRECT domain match; adjacent-only is
    partial. A looser line makes the model emit a verdict the validator
    rejects, and a rejection costs the whole paid call."""
    from jobhunt.matching.models import RoleVerdict
    text = flat(build_role_system(profile, CFG))
    floor = text[text.index("# the floor"):text.index("# verdicts")]
    verdicts = text[text.index("# verdicts"):]
    desc = flat(RoleVerdict.model_fields["role_fit"].description)
    assert "only a hybrid pass or a `direct` domain match may be `strong`" in floor
    assert "`direct` domain match" in verdicts.split("- `partial`")[0]
    assert "`adjacent`-only domain match" in verdicts.split("- `partial`")[1]
    assert "`direct` domain match" in desc.split("`partial`")[0]
    assert "`adjacent`-only domain match" in desc.split("`partial`")[1]
    # No statement of `strong` may name a bare "domain match".
    assert "matches a domain" not in verdicts
