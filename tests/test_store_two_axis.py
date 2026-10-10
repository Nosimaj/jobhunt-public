"""Storage for the two-axis verdicts.

Company verdicts are cached PER COMPANY, not per posting.

Measured 2026-09-28: 131 distinct companies across 132 candidates, so the cache
saves ONE call within a single HN thread. Its value is across months - repeat
posters - and in one company judgement that cannot drift between two postings
from the same employer.
"""

import pytest

from jobhunt.discovery.models import Posting
from jobhunt.discovery.store import Store
from jobhunt.matching.models import CompanyVerdict, RoleVerdict


def posting(pid="hn:1", company="Acme", normalised="acme", text="Acme | Engineer"):
    return Posting(id=pid, source="hn", source_thread_id="9", company=company,
                   company_normalised=normalised, raw_text=text, apply_url=None,
                   is_remote=True, geo_signal="us", posted_at="2026-09-01",
                   first_seen="x", last_seen="x", status="new")


def role_verdict(fit="partial"):
    return RoleVerdict(role_title="Design Engineer", discipline="design",
                       reason="right discipline", hybrid_test="n/a", domain_fit="none",
                       hybrid_evidence=[], overlaps=["design systems"],
                       gaps=["no hardware"], role_fit=fit)


def company_verdict(fit="target"):
    return CompanyVerdict(reason="builds bike computers", domain_pull="present",
                          designs_how_things_work="present",
                          design_valued="present", stage_fit="unknown",
                          reachability="unknown", disqualifier=None,
                          company_fit=fit)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    return s


def test_a_role_verdict_round_trips(store):
    store.upsert_posting(posting())
    store.save_role_verdict("hn:1", role_verdict(), model="m", prompt_sha="s")

    got = store.role_verdict_of("hn:1")
    assert got.role_fit == "partial"
    assert got.overlaps == ["design systems"]
    assert got.gaps == ["no hardware"]


def test_a_company_verdict_round_trips(store):
    store.save_company_verdict("acme", "Acme", company_verdict(),
                               model="m", prompt_sha="s")

    got = store.company_verdict_of("acme")
    assert got.company_fit == "target"
    assert got.domain_pull == "present"


def test_a_company_verdict_is_shared_by_every_posting_from_that_company(store):
    """The entire point of caching per company: two postings, one call."""
    store.upsert_posting(posting("hn:1", normalised="acme"))
    store.upsert_posting(posting("hn:2", normalised="acme"))
    store.save_company_verdict("acme", "Acme", company_verdict(),
                               model="m", prompt_sha="s")

    assert store.companies_needing_score("s") == []


def test_a_company_scored_against_another_prompt_needs_rescoring(store):
    store.upsert_posting(posting())
    store.save_company_verdict("acme", "Acme", company_verdict(),
                               model="m", prompt_sha="old")

    assert store.companies_needing_score("new") == ["acme"]


def test_companies_needing_score_lists_each_company_once(store):
    store.upsert_posting(posting("hn:1", normalised="acme"))
    store.upsert_posting(posting("hn:2", normalised="acme"))
    store.upsert_posting(posting("hn:3", company="Beta", normalised="beta"))

    assert sorted(store.companies_needing_score("s")) == ["acme", "beta"]


def test_companies_needing_score_ignores_rejected_postings(store):
    """Slice 2 already rejected these; scoring their companies is pure spend."""
    p = posting("hn:9", company="Gone", normalised="gone")
    p = p.model_copy(update={"status": "rejected", "reject_reason": "not_remote"})
    store.upsert_posting(p)

    assert "gone" not in store.companies_needing_score("s")


def test_the_action_is_stored_when_both_axes_are_present(store):
    store.upsert_posting(posting())
    store.save_company_verdict("acme", "Acme", company_verdict("target"),
                               model="m", prompt_sha="s")
    store.save_role_verdict("hn:1", role_verdict("no"), model="m", prompt_sha="s")

    store.refresh_actions()

    assert store.action_of("hn:1") == "outreach_lead"


def test_the_action_is_null_until_both_axes_exist(store):
    store.upsert_posting(posting())
    store.save_role_verdict("hn:1", role_verdict("strong"), model="m",
                            prompt_sha="s")

    store.refresh_actions()

    assert store.action_of("hn:1") is None


def test_company_source_text_aggregates_every_posting_for_that_company(store):
    store.upsert_posting(posting("hn:1", normalised="acme", text="first advert"))
    store.upsert_posting(posting("hn:2", normalised="acme", text="second advert"))

    text = store.company_source_text("acme")

    assert "first advert" in text and "second advert" in text


def test_postings_needing_role_score_respects_the_prompt_fingerprint(store):
    store.upsert_posting(posting())
    store.save_role_verdict("hn:1", role_verdict(), model="m", prompt_sha="old")

    assert [p.id for p in store.postings_needing_role_score("old")] == []
    assert [p.id for p in store.postings_needing_role_score("new")] == ["hn:1"]


def test_the_two_axis_columns_do_not_disturb_the_slice_two_columns(store):
    """Slice 2 owns `status`; a role verdict must not silently rewrite it."""
    store.upsert_posting(posting())
    store.save_role_verdict("hn:1", role_verdict(), model="m", prompt_sha="s")

    assert store.get("hn:1").status == "new"


def ats_role(rid="ashby:acme-co:1"):
    from jobhunt.discovery.models import AtsRole
    return AtsRole(id=rid, provider="ashby", token="acme-co", company="Acme",
                   title="Staff Product Designer", body="ats duties " * 40,
                   location="Remote", workplace_type="remote",
                   department="Design", apply_url=None, posted_at="2026-09-20")


def test_company_source_text_reads_only_the_hn_post(store):
    """Rubric B's source was always the HN post. This query was written when
    a company had exactly ONE posting; with ~25 ATS roles per company it
    would send ~52K tokens per company - turning "42 companies, cents" into
    dollars, and letting a large board blow the context window and 400."""
    store.upsert_posting(posting("hn:1", normalised="acme", text="the hn advert"))
    store.upsert_ats_role(ats_role(), now="2026-09-29")

    text = store.company_source_text("acme")

    assert "the hn advert" in text
    assert "ats duties" not in text


def test_a_v7_database_is_migrated_to_v8_and_round_trips_domain_fit(tmp_path):
    """The real database was stamped 7 before domain_fit existed. An applied
    migration never replays, so the columns must arrive via v8."""
    import sqlite3
    from jobhunt.discovery.store import SCHEMA_VERSION, Store
    path = tmp_path / "v7.sqlite"
    first = Store(path)
    first.init_schema()
    first._conn.execute("ALTER TABLE postings DROP COLUMN domain_fit")
    first._conn.execute("ALTER TABLE postings DROP COLUMN domain_evidence")
    first._conn.execute("PRAGMA user_version = 7")
    first._conn.commit()
    first._conn.close()
    s = Store(path)
    s.init_schema()
    assert s.schema_version() == SCHEMA_VERSION == 8
    cols = {r[1] for r in s._conn.execute("PRAGMA table_info(postings)")}
    assert {"domain_fit", "domain_evidence"} <= cols


def test_a_v6_database_walks_the_real_chain_to_v8(tmp_path):
    """Fresh builds skip _MIGRATIONS. Walk it from an older stamp, as the live
    database does, so a migration that references a missing column fails here."""
    from jobhunt.discovery.store import SCHEMA_VERSION, Store
    path = tmp_path / "v6.sqlite"
    first = Store(path)
    first.init_schema()
    for col in ("reach", "domain_fit", "domain_evidence"):
        first._conn.execute(f"ALTER TABLE postings DROP COLUMN {col}")
    first._conn.execute("PRAGMA user_version = 6")
    first._conn.commit()
    first._conn.close()
    s = Store(path)
    s.init_schema()
    assert s.schema_version() == SCHEMA_VERSION == 8
    cols = {r[1] for r in s._conn.execute("PRAGMA table_info(postings)")}
    assert {"reach", "domain_fit", "domain_evidence"} <= cols
    s.upsert_posting(posting())
    s._conn.execute("UPDATE postings SET reach='local', domain_fit='direct',"
                    " domain_evidence='[\"e\"]' WHERE id='hn:1'")
    row = s._conn.execute("SELECT reach, domain_fit FROM postings"
                          " WHERE id='hn:1'").fetchone()
    assert (row["reach"], row["domain_fit"]) == ("local", "direct")


def test_domain_fit_and_evidence_are_persisted(tmp_path):
    s = Store(tmp_path / "d.sqlite")
    s.init_schema()
    s.upsert_posting(posting())
    v = RoleVerdict(role_title="Platform Engineer", discipline="engineering",
                    reason="r", hybrid_test="fail", domain_fit="direct",
                    domain_evidence=["builds the clinician scheduling tool"],
                    overlaps=["a", "b"], role_fit="strong")
    s.save_role_verdict("hn:1", v, model="m", prompt_sha="s")
    row = s._conn.execute("SELECT domain_fit, domain_evidence FROM postings"
                          " WHERE id='hn:1'").fetchone()
    assert row["domain_fit"] == "direct"
    assert "clinician scheduling" in row["domain_evidence"]
    assert s.role_verdict_of("hn:1").domain_fit == "direct"
