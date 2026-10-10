import pytest
from pydantic import ValidationError
from jobhunt.matching.models import EvalReport, Label, MatchVerdict


def verdict(**kw):
    base = dict(role_title="Senior Design Engineer", discipline="design",
                hybrid_signal="explicit", reason="r", concern="a four-person team may not have a design practice",
                fit_score=78, bucket="strong")
    return MatchVerdict(**{**base, **kw})


def test_field_order_puts_reason_before_bucket():
    # The schema's field order IS the generation order: the model must reason
    # before it concludes, not conclude then justify.
    fields = list(MatchVerdict.model_fields)
    assert fields.index("reason") < fields.index("bucket")
    assert fields.index("concern") < fields.index("bucket")


def test_rejects_unknown_bucket_discipline_and_signal():
    for bad in (dict(bucket="great"), dict(discipline="marketing"),
                dict(hybrid_signal="vibes")):
        with pytest.raises(ValidationError):
            verdict(**bad)


def test_fit_score_is_bounded_0_to_100():
    for bad in (-1, 101):
        with pytest.raises(ValidationError):
            verdict(fit_score=bad)
    assert verdict(fit_score=0).fit_score == 0
    assert verdict(fit_score=100).fit_score == 100


def test_reason_and_concern_are_required_and_non_empty():
    for bad in (dict(reason=""), dict(concern="")):
        with pytest.raises(ValidationError):
            verdict(**bad)


def test_hybrid_signal_none_cannot_be_strong():
    """The load-bearing invariant from spec 3.1, enforced by the model itself."""
    with pytest.raises(ValidationError, match="hybrid"):
        verdict(hybrid_signal="none", bucket="strong")
    assert verdict(hybrid_signal="none", bucket="maybe").bucket == "maybe"


def test_location_and_salary_default_to_none():
    v = verdict()
    assert v.location is None and v.salary is None


def test_json_schema_is_generatable_for_output_format():
    # parse(output_format=MatchVerdict) requires a usable JSON schema.
    schema = MatchVerdict.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "reason" in schema["properties"]


def test_label_verdict_uses_the_same_bucket_vocabulary():
    assert Label(id="hn:1", company="Acme", verdict="no", criterion="x").verdict == "no"
    with pytest.raises(ValidationError):
        Label(id="hn:1", company="Acme", verdict="nope", criterion="x")


def test_a_real_but_short_concern_is_accepted():
    """The length floor is only there to block "None." A bar high enough to
    catch "None noted" would discard legitimate one-clause concerns, and a
    rejection costs the WHOLE verdict - which, combined with the run's
    three-strike abort, could kill a 132-posting run over phrasing."""
    for text in ("Salary is not disclosed.", "Remote policy unclear.",
                 "Highly competitive role."):
        assert verdict(concern=text).concern == text


@pytest.mark.parametrize("text", ["None.", "none", "N/A", "nothing",
                                  "No concerns", "Nothing significant"])
def test_a_non_concern_is_rejected_by_name(text):
    # `concern` is the load-bearing anti-inflation device (spec 5.3); "None."
    # satisfies any plausible length bar and defeats it entirely.
    with pytest.raises(ValidationError):
        verdict(concern=text)


# ---------------------------------------------------------------------------
# The gate swap, 2026-10-06. The hybrid floor becomes an either/or floor and
# applies ONLY to engineering-titled roles - a domain gate applied to every
# role would cut roughly 6 of the 17 current `strong`, including all three
# PermitFlow and all three Rho roles. He asked to INCLUDE relevant engineering
# work, not to EXCLUDE good design work.
# ---------------------------------------------------------------------------
from jobhunt.matching.models import RoleVerdict as _RoleVerdict


def _rv(**kw):
    base = dict(role_title="T", discipline="engineering", reason="r",
                hybrid_test="fail", hybrid_evidence=[], overlaps=["a", "b"],
                gaps=[], role_fit="partial", domain_fit="direct",
                domain_evidence=["builds the internal admin console"])
    return _RoleVerdict(**{**base, **kw})


def test_an_engineering_role_with_no_domain_and_no_hybrid_must_be_no():
    with pytest.raises(ValidationError, match="neither a hybrid pass nor"):
        _rv(hybrid_test="fail", domain_fit="none", role_fit="partial")


def test_an_engineering_role_earns_its_place_on_DOMAIN_alone():
    """THE change. Before 2026-10-06 this raised."""
    assert _rv(hybrid_test="fail", domain_fit="direct",
               role_fit="strong").role_fit == "strong"


def test_an_engineering_role_earns_its_place_on_ADJACENT_domain():
    assert _rv(hybrid_test="fail", domain_fit="adjacent",
               role_fit="partial").role_fit == "partial"


def test_an_engineering_role_still_earns_its_place_on_HYBRID_alone():
    assert _rv(hybrid_test="pass", hybrid_evidence=["designs the UI"],
               domain_fit="none", role_fit="strong").role_fit == "strong"


def test_a_weak_hybrid_still_caps_at_partial_even_with_a_direct_domain():
    with pytest.raises(ValidationError, match="weak hybrid"):
        _rv(hybrid_test="weak", domain_fit="direct", role_fit="strong")


@pytest.mark.parametrize("discipline", ["design", "design_engineering"])
def test_a_design_role_is_never_gated_by_domain(discipline):
    """This is what protects the PermitFlow and Rho roles."""
    assert _rv(discipline=discipline, hybrid_test="n/a",
               domain_fit="none", role_fit="strong").role_fit == "strong"


def test_a_direct_domain_requires_evidence():
    with pytest.raises(ValidationError, match="domain_evidence"):
        _rv(domain_fit="direct", domain_evidence=[])


def test_domain_none_needs_no_evidence():
    assert _rv(hybrid_test="pass", hybrid_evidence=["designs the UI"],
               domain_fit="none", domain_evidence=[]).domain_fit == "none"


def test_domain_fit_is_required():
    with pytest.raises(ValidationError):
        _RoleVerdict(role_title="T", discipline="design", reason="r",
                     hybrid_test="n/a", role_fit="strong")


def test_a_weak_hybrid_with_no_domain_may_still_be_partial():
    """Behaviour that predates the swap and must survive it."""
    assert _rv(hybrid_test="weak", domain_fit="none",
               role_fit="partial").role_fit == "partial"


def test_an_adjacent_only_match_caps_at_partial():
    with pytest.raises(ValidationError, match="adjacent-only"):
        _rv(hybrid_test="fail", domain_fit="adjacent",
            domain_evidence=["next to the domain"], role_fit="strong")


def test_adjacent_with_a_hybrid_pass_may_be_strong():
    assert _rv(hybrid_test="pass", hybrid_evidence=["designs the UI"],
               domain_fit="adjacent", role_fit="strong").role_fit == "strong"


def test_no_stale_hybrid_only_floor_wording_reaches_the_model():
    """The old rule ("fails the hybrid test => no") must not survive in any
    field description or in the rendered rubric."""
    import json
    from jobhunt import config
    from jobhunt.matching.rubrics import build_role_system
    from jobhunt.matching.search_config import load_search_config
    for name, f in _RoleVerdict.model_fields.items():
        assert "fails the hybrid test" not in (f.description or "").casefold(), name
    if config.PROFILE_PATH.is_file():
        profile = json.loads(config.PROFILE_PATH.read_text())
        text = " ".join(build_role_system(
            profile, load_search_config()).split()).casefold()
        assert "fails the hybrid test" not in text
