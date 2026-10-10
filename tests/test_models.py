import pytest
from pydantic import ValidationError
from jobhunt.models import Bullet, Provenance, Role, Profile, Identity, Summaries


def _prov(source="resume"):
    return Provenance(source=source, snapshot_date="2026-09-19")


def test_bullet_rejects_unknown_audience():
    with pytest.raises(ValidationError):
        Bullet(id="b1", text="x", audience="marketing",
               user_approved=True, provenance=_prov())


def test_role_defaults_to_feature_render():
    role = Role(id="tidewater-2016", company="Tidewater Corporation",
                title_of_record="Sr Product Designer & Prototyper",
                title_presented="UX Designer — Connected Products",
                start="2016-12", end="2019-11")
    assert role.render == "feature"
    assert role.bullets == []


def test_role_rejects_unknown_render():
    with pytest.raises(ValidationError):
        Role(id="x", company="c", title_of_record="t", title_presented="t",
             start="2020-01", end="2020-02", render="hidden")


def test_bullet_rejects_both_audience_from_pdf_source():
    with pytest.raises(ValidationError):
        Bullet(id="b1", text="x", audience="both",
               user_approved=False, provenance=_prov(source="pdf"))


def test_profile_requires_explicit_complete_flag():
    with pytest.raises(ValidationError):
        Profile(generated_at="2026-09-19T00:00:00", sources=[],
                identity=Identity(name="n", headline="h", location="l",
                                  email="e", links={}),
                summaries=Summaries(design="d", eng="e"), skills=[], roles=[])


def test_review_report_rejects_a_non_blocking_blocking_gap():
    """`blocking_gaps` is blocking by definition; the name IS the contract.

    Consumers must not re-filter it on `Gap.blocking`, so a non-blocking entry
    must be impossible to construct rather than silently ignored downstream.
    """
    from jobhunt.models import Gap, ReviewReport

    ok = ReviewReport(blocking_gaps=[
        Gap(kind="missing-dates", detail="d", blocking=True)])
    assert len(ok.blocking_gaps) == 1

    with pytest.raises(ValidationError, match="blocking=True"):
        ReviewReport(blocking_gaps=[
            Gap(kind="chronology", detail="d", blocking=False)])
