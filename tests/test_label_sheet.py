"""The labelling-sheet generator.

Reproducibility is the property that matters: the first eval set records its
seed so the sample can be re-derived and defended. An unseeded sample cannot
be audited later.
"""

from jobhunt.discovery.models import Posting
from jobhunt.matching.label_sheet import ROLE_SIGNALS, build_sheet, select


def posting(pid, company, text):
    return Posting(id=pid, source="hn", source_thread_id="9", company=company,
                   company_normalised=company.casefold(), raw_text=text,
                   apply_url=None, is_remote=True, geo_signal="us",
                   posted_at="2026-09-01", first_seen="x", last_seen="x",
                   status="new")


DESIGN = posting("hn:1", "Odin", "Senior Design Engineer. You will own the "
                                 "design system and run usability testing.")
BACKEND = posting("hn:2", "Yeet", "Backend Engineer, Rust and Linux BPF.")
PERKS = posting("hn:3", "FlixTrain", "Backend Engineer. Perks: mental health "
                                     "support, travel vouchers.")


def test_a_design_signal_is_detected():
    assert select([DESIGN, BACKEND], seed=1, random_n=0).enriched == [DESIGN]


def test_a_pure_backend_posting_carries_no_role_signal():
    assert BACKEND not in select([DESIGN, BACKEND], seed=1, random_n=0).enriched


def test_benefits_text_does_not_create_a_role_signal():
    """FlixTrain matched the health DOMAIN on 'mental health support' in its
    perks list. The role signals must key on titles and duties only, or the
    sheet fills up with postings that mention design nowhere."""
    assert PERKS not in select([DESIGN, BACKEND, PERKS], seed=1, random_n=0).enriched


def test_the_random_slice_is_reproducible():
    pool = [posting(f"hn:{i}", f"Co{i}", "Backend Engineer") for i in range(20)]
    a = select(pool, seed=42, random_n=5).random
    b = select(pool, seed=42, random_n=5).random
    assert [p.id for p in a] == [p.id for p in b]


def test_a_different_seed_gives_a_different_slice():
    pool = [posting(f"hn:{i}", f"Co{i}", "Backend Engineer") for i in range(20)]
    a = select(pool, seed=1, random_n=5).random
    b = select(pool, seed=2, random_n=5).random
    assert [p.id for p in a] != [p.id for p in b]


def test_the_random_slice_never_repeats_an_enriched_posting():
    pool = [DESIGN] + [posting(f"hn:{i}", f"Co{i}", "Backend Engineer")
                       for i in range(10)]
    chosen = select(pool, seed=7, random_n=5)
    assert not ({p.id for p in chosen.random} & {p.id for p in chosen.enriched})


def test_already_labelled_postings_are_excluded():
    chosen = select([DESIGN, BACKEND], seed=1, random_n=5, exclude={"hn:1"})
    assert "hn:1" not in {p.id for p in chosen.enriched + chosen.random}


def test_the_sheet_asks_for_both_axes():
    text = build_sheet(select([DESIGN], seed=1, random_n=0), seed=1)
    assert "Role:" in text and "Company:" in text


def test_the_sheet_records_that_the_sample_is_enriched():
    """An enriched sample can measure agreement but CANNOT estimate base
    rates. If that caveat is not in the file it will be forgotten."""
    text = build_sheet(select([DESIGN], seed=1, random_n=0), seed=1)
    assert "enriched" in text.casefold()
    assert "base rate" in text.casefold()


def test_the_sheet_contains_no_model_verdict():
    """Blind labelling was the highest-leverage step in slice 3. A sheet that
    shows a model's opinion anchors the human and destroys the ground truth."""
    text = build_sheet(select([DESIGN], seed=1, random_n=0), seed=1)
    for leak in ("role_fit", "company_fit", "apply_priority", "fit_score"):
        assert leak not in text


def test_the_sheet_includes_the_posting_text():
    text = build_sheet(select([DESIGN], seed=1, random_n=0), seed=1)
    assert "usability testing" in text
