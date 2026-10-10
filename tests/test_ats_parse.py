"""Normalising three provider JSON shapes into one AtsRole.

Fixtures are REAL responses, trimmed to three roles each. Every gotcha below
was found by probing the live APIs on 2026-09-28/29.
"""

import json
from pathlib import Path

import pytest

from jobhunt.discovery.ats_parse import parse, strip_html

FIX = Path(__file__).parent / "fixtures" / "ats"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def gh():
    return parse("greenhouse", "wikimedia", "Wikimedia Foundation",
                 load("greenhouse-wikimedia.json"))


def ashby():
    return parse("ashby", "runway-ml", "Runway", load("ashby-runway.json"))


def lever():
    return parse("lever", "findigs", "Findigs", load("lever-findigs.json"))


@pytest.mark.parametrize("roles", [gh(), ashby(), lever()])
def test_every_provider_yields_roles_with_a_title_and_a_body(roles):
    assert roles
    for r in roles:
        assert r.title.strip()
        # Rubric A applies the Hybrid Test to DUTIES. A title alone is
        # useless, so an empty body is a parse failure, not a thin advert.
        assert len(r.body) > 200


@pytest.mark.parametrize("roles", [gh(), ashby(), lever()])
def test_the_id_is_namespaced_by_provider_and_token(roles):
    """Review Focus 3. Greenhouse and Lever both use bare integers, so two
    companies can share id 4001."""
    for r in roles:
        assert r.id.count(":") == 2
        assert r.id.split(":")[0] in ("greenhouse", "ashby", "lever")


def test_greenhouse_content_is_double_unescaped_and_stripped():
    """Review Focus 2. Greenhouse `content` arrives as `&lt;p&gt;&lt;strong&gt;`
    - escaped tags inside escaped text. One unescape leaves literal <p> tags
    in the body, which the model then reads as content."""
    body = gh()[0].body
    assert "&lt;" not in body and "<p>" not in body and "&amp;" not in body


def test_stripping_is_not_run_further_than_the_provider_requires():
    """Also Review Focus 2. Unescaping is NOT idempotent: a literal
    `&amp;lt;div&amp;gt;` in prose becomes `<div>` on the second pass and the
    tag stripper then deletes it. Text that is already clean must survive."""
    assert strip_html("cost is 10 &amp; rising") == "cost is 10 & rising"
    assert "keep me" in strip_html("<p>keep me</p>")


def test_lever_duties_come_from_lists_not_just_the_description():
    """Measured on findigs: descriptionPlain 2,605 chars, lists 4,227. The
    responsibilities and requirements - exactly what the Hybrid Test reads -
    live in `lists`. Using descriptionPlain alone silently drops them and
    every Lever engineering role fails the Hybrid Test for lack of evidence."""
    role = lever()[0]
    raw = load("lever-findigs.json")[0]
    assert len(role.body) > len(raw.get("descriptionPlain") or "")


def test_lever_lists_are_rendered_as_text_not_as_a_python_repr():
    body = lever()[0].body
    assert "{'text'" not in body and "[{" not in body


def test_ashby_uses_its_structured_remote_flag():
    """Rewritten per review: asserting only Literal membership would pass
    even if `_ashby` hardcoded workplace_type="unknown" for every job and
    ignored `workplaceType`/`isRemote` entirely. Assert the ACTUAL mapping
    against the committed fixture's real values, plus synthetic cases
    pinning the isRemote fallback path."""
    raw_jobs = load("ashby-runway.json")["jobs"]
    roles = ashby()
    assert len(roles) == len(raw_jobs)
    for role, job in zip(roles, raw_jobs):
        # Every job in the committed fixture has workplaceType "Remote" -
        # assert the parser actually reads it (casefolded), not a
        # hardcoded/default value that happens to coincide.
        assert job["workplaceType"].casefold() == "remote"
        assert role.workplace_type == "remote"

    # Synthetic: no workplaceType, but isRemote true -> "remote" fallback.
    fallback_remote = parse("ashby", "t", "Co", {"jobs": [{
        "id": 1, "title": "Engineer", "isRemote": True,
        "descriptionPlain": "duties " * 60}]})
    assert fallback_remote[0].workplace_type == "remote"

    # Synthetic: neither workplaceType nor isRemote -> "unknown".
    fallback_unknown = parse("ashby", "t", "Co", {"jobs": [{
        "id": 2, "title": "Engineer", "descriptionPlain": "duties " * 60}]})
    assert fallback_unknown[0].workplace_type == "unknown"


def test_ashby_description_plain_is_not_double_stripped():
    """Review Focus 2, Ashby-specific. `descriptionPlain` is already plain
    text. If a future edit routes it through `strip_html`, a literal
    `&amp;lt;div&amp;gt;` becomes `<div>` on the second unescape pass and the
    tag stripper then deletes it, and a bare `<` used as a less-than sign
    would be swallowed as a tag opener. This test must fail if the Ashby
    branch is ever wrapped in `strip_html`."""
    raw = ("Budget is < $5k. Note: &amp;lt;div&amp;gt; is a literal "
           "example, not markup. ") * 5
    payload = {"jobs": [{"id": 1, "title": "Engineer",
                          "descriptionPlain": raw}]}
    body = parse("ashby", "t", "Co", payload)[0].body
    assert body == raw.strip()
    assert "&amp;lt;div&amp;gt;" in body
    assert "<div>" not in body
    assert "< $5k" in body


@pytest.mark.parametrize("roles", [gh(), ashby(), lever()])
def test_an_apply_url_is_captured(roles):
    assert any(r.apply_url for r in roles)


def test_a_missing_location_becomes_empty_not_none():
    """Review Focus 4. Absence of a location is not evidence of a foreign one;
    downstream must be able to tell 'no location given' from 'London'."""
    payload = {"jobs": [{"id": 1, "title": "Engineer", "content": "duties " * 60}]}
    assert parse("greenhouse", "t", "Co", payload)[0].location == ""


def test_an_unknown_provider_is_an_error():
    with pytest.raises(ValueError, match="unknown provider"):
        parse("workday", "t", "Co", {})


# --- Workable and Teamtailor (probed live 2026-10-01) -----------------------

def workable():
    return parse("workable", "seeq", "Seeq", load("workable-seeq.json"))


def teamtailor():
    return parse("teamtailor", "axmed", "Axmed", load("teamtailor-axmed.json"))


@pytest.mark.parametrize("provider,roles", [("workable", workable()),
                                            ("teamtailor", teamtailor())])
def test_new_providers_yield_titled_bodied_namespaced_roles(provider, roles):
    assert len(roles) == 3
    for r in roles:
        assert r.title.strip()
        assert len(r.body) > 200
        assert "<" not in r.body and "&lt;" not in r.body
        assert r.id.startswith(f"{provider}:") and r.id.count(":") == 2
        assert r.location is not None
        assert r.workplace_type in ("remote", "onsite", "hybrid", "unknown")
        assert len(r.posted_at) == 10 and r.posted_at[4] == "-"
        assert r.apply_url


def test_workable_maps_the_boolean_telecommuting_flag():
    raw = load("workable-seeq.json")["jobs"]
    for role, job in zip(workable(), raw):
        assert role.id == f"workable:seeq:{job['shortcode']}"
        assert role.workplace_type == ("remote" if job["telecommuting"] else "unknown")
    assert any(r.workplace_type == "remote" for r in workable())
    body = "duties " * 60
    off = parse("workable", "t", "Co", {"jobs": [
        {"shortcode": "A", "title": "E", "description": body,
         "telecommuting": False}]})
    assert off[0].workplace_type == "unknown"


def test_workable_location_joins_present_parts_and_is_never_none():
    def one(**kw):
        job = {"shortcode": "A", "title": "E", "description": "x " * 200, **kw}
        return parse("workable", "t", "Co", {"jobs": [job]})[0].location
    assert one(city="Austin", state="TX", country="United States") == \
        "Austin, TX, United States"
    assert one(city=None, state="", country="Australia") == "Australia"
    assert one() == ""
    assert one(city=None, state=None, country=None) == ""


def test_workable_description_is_unescaped_exactly_once():
    desc = "<p>Budget &amp;lt;div&amp;gt; stays literal. " + "duty " * 60 + "</p>"
    body = parse("workable", "t", "Co", {"jobs": [
        {"shortcode": "A", "title": "E", "description": desc}]})[0].body
    assert "&lt;div&gt;" in body and "<div>" not in body


def test_teamtailor_content_is_unescaped_exactly_once():
    html_ = "<p>Budget &amp;lt;div&amp;gt; stays literal. " + "duty " * 60 + "</p>"
    body = parse("teamtailor", "t", "Co", {"items": [
        {"id": "1", "title": "E", "content_html": html_}]})[0].body
    assert "&lt;div&gt;" in body and "<div>" not in body


def test_teamtailor_location_prefers_locality_region_country():
    raw = load("teamtailor-axmed.json")["items"][0]
    addr = raw["_jobposting"]["jobLocation"][0]["address"]
    loc = teamtailor()[0].location
    assert addr["addressLocality"] in loc and addr["addressCountry"] in loc


def test_teamtailor_location_falls_back_and_is_defensive():
    def loc(posting):
        item = {"id": "1", "title": "E", "content_html": "x " * 200,
                "date_published": "2026-09-18T12:24:52+01:00"}
        if posting is not ...:
            item["_jobposting"] = posting
        r = parse("teamtailor", "t", "Co", {"items": [item]})[0]
        assert r.department == "" and r.posted_at == "2026-09-18"
        return r.location
    street = {"jobLocation": [{"address": {"streetAddress": "1 Main St"}}]}
    assert loc(street) == "1 Main St"
    assert loc(...) == ""
    assert loc({}) == ""
    assert loc({"jobLocation": "Berlin"}) == ""
    assert loc({"jobLocation": []}) == ""
    assert loc({"jobLocation": [None]}) == ""
    assert loc({"jobLocation": [{"address": None}]}) == ""
    assert loc(None) == ""


def test_new_provider_tokens_are_extracted_boundary_safe():
    from jobhunt.discovery.ats_token import extract
    assert extract("apply at https://apply.workable.com/seeq/.") == ("workable", "seeq")
    assert extract("https://apply.workable.com/atria-health/j/ABC/") == \
        ("workable", "atria-health")
    assert extract("see https://axmed.teamtailor.com/jobs now") == \
        ("teamtailor", "axmed")
    assert extract("Apply: axmed.teamtailor.com/jobs/8175859-a") == \
        ("teamtailor", "axmed")
    # Ashby still wins when a post links several boards.
    assert extract("https://jobs.ashbyhq.com/x https://apply.workable.com/y") == \
        ("ashby", "x")


def test_teamtailor_pattern_ignores_a_bare_na_host_mid_sentence():
    """`na` is a false positive: it would be polled as a board every run."""
    from jobhunt.discovery.ats_token import extract
    assert extract("pages on na.teamtailor.com are regional") is None
    assert extract("see careers.na.teamtailor.com/jobs") is None
