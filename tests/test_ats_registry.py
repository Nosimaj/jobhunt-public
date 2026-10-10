"""Storing ATS board tokens against companies."""

import pytest

from jobhunt.discovery.models import Posting
from jobhunt.discovery.store import Store


def posting(pid, company, normalised, text):
    return Posting(id=pid, source="hn", source_thread_id="9", company=company,
                   company_normalised=normalised, raw_text=text, apply_url=None,
                   is_remote=True, geo_signal="us", posted_at="2026-09-01",
                   first_seen="x", last_seen="x", status="new")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    return s


def test_schema_version_is_eight(store):
    assert store.schema_version() == 8


def test_a_token_round_trips(store):
    store.set_ats_token("acme", "Acme", "ashby", "acme-co")
    assert store.companies_with_boards() == [("acme", "Acme", "ashby", "acme-co")]


def test_setting_a_token_twice_does_not_duplicate_the_company(store):
    store.set_ats_token("acme", "Acme", "ashby", "acme-co")
    store.set_ats_token("acme", "Acme", "greenhouse", "acme")
    rows = store.companies_with_boards()
    assert len(rows) == 1
    assert rows[0][2:] == ("greenhouse", "acme")


def test_a_company_with_no_token_is_not_listed(store):
    store.upsert_posting(posting("hn:1", "Acme", "acme", "no board here"))
    assert store.companies_with_boards() == []


def test_setting_a_token_preserves_an_existing_company_verdict(store):
    """The registry row is shared with rubric B. Writing a token must not
    erase a company verdict that cost an API call to produce."""
    from jobhunt.matching.models import CompanyVerdict
    verdict = CompanyVerdict(reason="builds real things", domain_pull="present",
                             designs_how_things_work="present",
                             design_valued="present", stage_fit="unknown",
                             reachability="unknown", disqualifier=None,
                             company_fit="target")
    store.save_company_verdict("acme", "Acme", verdict, model="m", prompt_sha="s")

    store.set_ats_token("acme", "Acme", "ashby", "acme-co")

    assert store.company_verdict_of("acme").company_fit == "target"


def test_tokens_are_harvested_from_rejected_postings_too(store):
    """The three best design roles in the September thread were at companies
    whose HN post was rejected not_remote - Smarkets, Baton, Tandem Health.
    Harvesting only candidates would lose exactly the companies whose BOARDS
    carry the design roles this slice exists to find."""
    from jobhunt.discovery.ats_token import harvest
    p = posting("hn:9", "Baton", "baton",
                "Baton | SF (Hybrid) | https://boards.greenhouse.io/baton")
    p = p.model_copy(update={"status": "rejected", "reject_reason": "not_remote"})
    store.upsert_posting(p)

    harvest(store)

    assert store.companies_with_boards() == [("baton", "Baton", "greenhouse", "baton")]


# --- a seeded registry row is NOT a scored company ------------------------
#
# `set_ats_token` has to write SOMETHING into the NOT NULL rubric-B columns,
# so it seeds 'neutral' / 'not yet scored'. Only `companies_needing_score`
# tested for that; every other reader took the placeholder at face value and
# a company nobody had judged acquired a verdict, a count and an action.
# `scored_at = ''` is the sentinel: it is what the seed already writes.

def company_verdict(fit="target"):
    from jobhunt.matching.models import CompanyVerdict
    return CompanyVerdict(reason="builds real things", domain_pull="present",
                          designs_how_things_work="present",
                          design_valued="present", stage_fit="unknown",
                          reachability="unknown", disqualifier=None,
                          company_fit=fit)


def role_verdict(fit="strong"):
    from jobhunt.matching.models import RoleVerdict
    return RoleVerdict(role_title="Design Engineer", discipline="design",
                       reason="right discipline", hybrid_test="n/a", domain_fit="none",
                       hybrid_evidence=[], overlaps=["design systems"],
                       gaps=[], role_fit=fit)


def test_a_seeded_company_yields_no_action_even_with_a_strong_role(store):
    """The critical seam. derive_action('strong','neutral') is 'apply', and
    the seeded 'neutral' is a placeholder, not a judgement - so an `apply`
    would be minted for a company nobody ever scored and shown forever by
    `--show-actions`."""
    store.upsert_posting(posting("hn:1", "Acme", "acme", "Acme | Engineer"))
    store.set_ats_token("acme", "Acme", "ashby", "acme-co")
    store.save_role_verdict("hn:1", role_verdict("strong"), model="m",
                            prompt_sha="s")

    store.refresh_actions()

    assert store.action_of("hn:1") is None


def test_a_real_verdict_after_seeding_does_produce_an_action(store):
    """The other half: once rubric B has actually run, the action appears."""
    store.upsert_posting(posting("hn:1", "Acme", "acme", "Acme | Engineer"))
    store.set_ats_token("acme", "Acme", "ashby", "acme-co")
    store.save_role_verdict("hn:1", role_verdict("strong"), model="m",
                            prompt_sha="s")
    store.save_company_verdict("acme", "Acme", company_verdict("target"),
                               model="m", prompt_sha="s")

    store.refresh_actions()

    assert store.action_of("hn:1") == "apply_priority"


def test_count_companies_counts_only_scored_rows(store):
    """`--status` printed 'companies scored: 42' when the answer was 0."""
    store.set_ats_token("acme", "Acme", "ashby", "acme-co")
    assert store.count_companies() == 0

    store.save_company_verdict("acme", "Acme", company_verdict(),
                               model="m", prompt_sha="s")
    assert store.count_companies() == 1


def test_company_verdict_of_returns_none_for_a_seeded_row(store):
    store.set_ats_token("acme", "Acme", "ashby", "acme-co")
    assert store.company_verdict_of("acme") is None


def test_seeding_does_not_hide_a_company_from_companies_needing_score(store):
    """This already worked and must keep working: the seed's prompt_sha is ''
    so it never matches a real sha."""
    store.upsert_posting(posting("hn:1", "Acme", "acme", "Acme | Engineer"))
    store.set_ats_token("acme", "Acme", "ashby", "acme-co")

    assert store.companies_needing_score("s") == ["acme"]
