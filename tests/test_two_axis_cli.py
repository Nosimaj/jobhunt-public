"""`python -m jobhunt.matching --two-axis` and its reporting.

Runs ALONGSIDE the original scorer rather than replacing it, so the cut-over
is a decision rather than a side effect of merging.
"""

import json
from pathlib import Path

import pytest

from jobhunt.discovery.models import Posting
from jobhunt.discovery.store import Store
from jobhunt.matching.match import main
from jobhunt.matching.models import CompanyVerdict, RoleVerdict

PROFILE = Path("tests/fixtures/profile_golden.json")
pytestmark = pytest.mark.skipif(
    not PROFILE.is_file(),
    reason="run `python -m jobhunt.ingest` first to generate data/profile.json")


def posting(pid, company="Acme", normalised="acme"):
    return Posting(id=pid, source="hn", source_thread_id="9", company=company,
                   company_normalised=normalised,
                   raw_text=f"{company} | Staff Design Engineer | Remote US\n"
                            "You will own the product experience end to end.",
                   apply_url=None, is_remote=True, geo_signal="us",
                   posted_at="2026-09-01", first_seen="x", last_seen="x",
                   status="new")


ROLE = RoleVerdict(role_title="Staff Design Engineer",
                   discipline="design_engineering", reason="right discipline",
                   hybrid_test="n/a", domain_fit="none", hybrid_evidence=[],
                   overlaps=["design systems", "Python"], gaps=[],
                   role_fit="strong")
COMPANY = CompanyVerdict(reason="builds a real product", domain_pull="present",
                         designs_how_things_work="present",
                         design_valued="present", stage_fit="unknown",
                         reachability="unknown", disqualifier=None,
                         company_fit="target")


class FakeBlock:
    def __init__(self, parsed): self.type = "text"; self.parsed_output = parsed
class FakeMsg:
    def __init__(self, parsed):
        self.content = [FakeBlock(parsed)]; self.stop_reason = "end_turn"
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5,
                                    "cache_read_input_tokens": 100})()
class FakeMessages:
    def __init__(self): self.calls = []
    def parse(self, **kw):
        self.calls.append(kw)
        fmt = kw["output_format"]
        return FakeMsg(ROLE if fmt is RoleVerdict else COMPANY)
class FakeClient:
    def __init__(self): self.messages = FakeMessages()


def db_with(tmp_path, postings):
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    for p in postings:
        s.upsert_posting(p)
    return s


def run(tmp_path, *extra, client=None):
    return main(["--db", str(tmp_path / "j.sqlite"), *extra],
                client=client if client is not None else FakeClient())


def test_two_axis_scores_both_axes_and_derives_the_action(tmp_path):
    db_with(tmp_path, [posting("hn:1")])
    c = FakeClient()

    assert run(tmp_path, "--two-axis", client=c) == 0

    s = Store(tmp_path / "j.sqlite")
    assert s.role_verdict_of("hn:1").role_fit == "strong"
    assert s.company_verdict_of("acme").company_fit == "target"
    assert s.action_of("hn:1") == "apply_priority"


def test_a_company_is_scored_once_for_many_postings(tmp_path):
    """The economy of the second axis: three postings, one company call."""
    db_with(tmp_path, [posting("hn:1"), posting("hn:2"), posting("hn:3")])
    c = FakeClient()

    run(tmp_path, "--two-axis", client=c)

    from jobhunt.matching.models import CompanyVerdict as CV
    company_calls = [k for k in c.messages.calls if k["output_format"] is CV]
    assert len(company_calls) == 1


def test_a_second_run_rescores_nothing(tmp_path):
    db_with(tmp_path, [posting("hn:1")])
    run(tmp_path, "--two-axis", client=FakeClient())

    c = FakeClient()
    run(tmp_path, "--two-axis", client=c)

    assert c.messages.calls == []


def test_rejected_postings_are_never_scored(tmp_path):
    p = posting("hn:9", company="Gone", normalised="gone").model_copy(
        update={"status": "rejected", "reject_reason": "not_remote"})
    db_with(tmp_path, [p])
    c = FakeClient()

    run(tmp_path, "--two-axis", client=c)

    assert c.messages.calls == []


def test_two_axis_does_not_touch_the_original_match_columns(tmp_path):
    """Both paths coexist until a cut-over is chosen deliberately."""
    db_with(tmp_path, [posting("hn:1")])

    run(tmp_path, "--two-axis", client=FakeClient())

    s = Store(tmp_path / "j.sqlite")
    assert s.get("hn:1").status == "new"
    assert s.verdict_of("hn:1") is None


def test_show_actions_lists_postings_by_action(tmp_path, capsys):
    db_with(tmp_path, [posting("hn:1")])
    run(tmp_path, "--two-axis", client=FakeClient())
    capsys.readouterr()

    run(tmp_path, "--show-actions")

    out = capsys.readouterr().out
    assert "apply_priority" in out and "Acme" in out


def test_show_actions_ranks_remote_then_commutable_then_the_rest(tmp_path, capsys):
    """Regression guard for the commute-ranking wiring in `_cmd_show_actions`.

    A reviewer built this exact fixture by hand, mutated `rank()` to `return
    0` unconditionally, and found nothing in the suite failed - the row
    count is unchanged either way, so only an order assertion catches it.
    Five postings land in the SAME action bucket (`apply_priority`, via one
    `strong`/`target` verdict each) at five different locations; only the
    printed ORDER of company names distinguishes correct ranking from none.
    """
    companies = [
        ("SFCo", "sfco", "San Francisco, CA"),
        ("RemoteCo", "remoteco", "Remote"),
        ("BostonCo", "bostonco", "Boston, MA"),
        ("AustinCo", "austinco", "Austin, TX"),
        ("CambridgeCo", "cambridgeco", "Cambridge, MA"),
    ]
    postings = [posting(f"hn:{norm}", company=name, normalised=norm)
               for name, norm, _loc in companies]
    db_with(tmp_path, postings)
    store = Store(tmp_path / "j.sqlite")
    for name, norm, loc in companies:
        v = ROLE.model_copy(update={"location": loc})
        store.save_role_verdict(f"hn:{norm}", v, model="m", prompt_sha="s")
        store.save_company_verdict(norm, name, COMPANY, model="m", prompt_sha="s")
    store.refresh_actions()
    capsys.readouterr()

    run(tmp_path, "--show-actions")

    out = capsys.readouterr().out
    pos = {name: out.index(name) for name, _norm, _loc in companies}
    # Remote first...
    assert pos["RemoteCo"] < pos["BostonCo"]
    assert pos["RemoteCo"] < pos["CambridgeCo"]
    assert pos["RemoteCo"] < pos["SFCo"]
    assert pos["RemoteCo"] < pos["AustinCo"]
    # ...then commutable (Boston-area)...
    assert pos["BostonCo"] < pos["SFCo"]
    assert pos["BostonCo"] < pos["AustinCo"]
    assert pos["CambridgeCo"] < pos["SFCo"]
    assert pos["CambridgeCo"] < pos["AustinCo"]


def test_status_reports_the_two_axis_counts(tmp_path, capsys):
    db_with(tmp_path, [posting("hn:1")])
    run(tmp_path, "--two-axis", client=FakeClient())
    capsys.readouterr()

    run(tmp_path, "--status")

    out = capsys.readouterr().out
    assert "companies scored" in out
    assert "apply_priority" in out


# --- findings from the duplication/conflict review -----------------------

def test_location_and_salary_survive_a_role_verdict(tmp_path):
    """RoleVerdict carries location and salary and the model pays to generate
    them; save_role_verdict dropped both, and role_verdict_of read them back
    as whatever the OLD path happened to have written."""
    db_with(tmp_path, [posting("hn:1")])
    s = Store(tmp_path / "j.sqlite")
    v = ROLE.model_copy(update={"location": "Remote US", "salary": "$180k"})
    s.save_role_verdict("hn:1", v, model="m", prompt_sha="s")

    got = s.role_verdict_of("hn:1")
    assert got.location == "Remote US"
    assert got.salary == "$180k"


def test_two_axis_honours_limit(tmp_path):
    """`--limit 5 --two-axis` must be a cheap trial, not a full $2-3 run.
    match.py already carries the comment 'silently-ignored flags are worse
    than rejected ones' about exactly this shape of mistake."""
    db_with(tmp_path, [posting(f"hn:{i}", company=f"Co{i}",
                               normalised=f"co{i}") for i in range(6)])
    c = FakeClient()

    run(tmp_path, "--two-axis", "--limit", "2", client=c)

    from jobhunt.matching.models import RoleVerdict as RV
    role_calls = [k for k in c.messages.calls if k["output_format"] is RV]
    assert len(role_calls) == 2


def test_two_axis_with_limit_only_scores_the_companies_it_needs(tmp_path):
    """Scoring 131 companies for a 2-posting trial would defeat the point."""
    db_with(tmp_path, [posting(f"hn:{i}", company=f"Co{i}",
                               normalised=f"co{i}") for i in range(6)])
    c = FakeClient()

    run(tmp_path, "--two-axis", "--limit", "2", client=c)

    from jobhunt.matching.models import CompanyVerdict as CV
    company_calls = [k for k in c.messages.calls if k["output_format"] is CV]
    assert len(company_calls) == 2


def test_rescore_is_rejected_with_two_axis(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, "--two-axis", "--rescore")


# --- --title-match: score one tier of the corpus at a time ----------------
# Measured 2026-10-01 over 462 scored ATS roles: design-titled roles hit
# 31.6%, hybrid titles 18.8%, engineering-only 3.4% and the residue 0/224.
# Scoring every surviving role at once costs ~$30 to buy most of its hits
# from the two cheapest buckets, so the tiers need to be runnable separately.
# (One untiered run over the live corpus is ~$50, not the ~$30 first quoted:
#  the queue grew, and a prompt-fingerprint change re-enters scored rows.)

def titled(store, **by_id):
    for pid, title in by_id.items():
        store._conn.execute("UPDATE postings SET role_title=? WHERE id=?",
                            (title, pid))
    store._conn.commit()


def tiered_db(tmp_path):
    s = db_with(tmp_path, [posting(f"hn:{i}", company=f"Co{i}",
                                   normalised=f"co{i}") for i in range(4)])
    titled(s, **{"hn:0": "Senior Product Designer",
                 "hn:1": "Design Engineer",
                 "hn:2": "Staff Software Engineer",
                 "hn:3": "Director of Partnerships"})
    s.close()
    return s


def _role_calls(client):
    from jobhunt.matching.models import RoleVerdict as RV
    return [k for k in client.messages.calls if k["output_format"] is RV]


def test_title_match_scores_only_the_matching_titles(tmp_path):
    tiered_db(tmp_path)
    c = FakeClient()

    run(tmp_path, "--two-axis", "--title-match", r"design|designer", client=c)

    assert len(_role_calls(c)) == 2


def test_title_match_is_case_insensitive(tmp_path):
    """Titles are human-typed; `DESIGN ENGINEER` and `Design Engineer` are
    the same role."""
    tiered_db(tmp_path)
    c = FakeClient()

    run(tmp_path, "--two-axis", "--title-match", r"DESIGN", client=c)

    assert len(_role_calls(c)) == 2


def test_title_match_narrows_the_company_axis_too(tmp_path):
    """Same reasoning as `--limit`: narrowing only the postings would still
    score every company and cost the full amount."""
    tiered_db(tmp_path)
    c = FakeClient()

    run(tmp_path, "--two-axis", "--title-match", r"design", client=c)

    from jobhunt.matching.models import CompanyVerdict as CV
    assert len([k for k in c.messages.calls if k["output_format"] is CV]) == 2


def test_title_match_still_applies_the_prefilter_corpus_wide(tmp_path):
    """The prefilter decision is a property of the title, not of the tier
    being run. Computing it only for the tier would leave the rest of the
    corpus unaudited and make `--show-dropped` depend on run order."""
    s = db_with(tmp_path, [posting("hn:0", company="A", normalised="a"),
                           posting("hn:1", company="B", normalised="b")])
    titled(s, **{"hn:0": "Senior Product Designer", "hn:1": "HVAC Technician"})
    s.close()

    run(tmp_path, "--two-axis", "--title-match", r"designer", client=FakeClient())

    s2 = Store(tmp_path / "j.sqlite")
    dropped = {pid: reason for pid, _title, reason in s2.dropped_postings()}
    assert dropped["hn:1"].startswith("technician")
    assert "hn:0" not in dropped
    s2.close()


def test_a_posting_with_no_title_is_not_matched(tmp_path):
    """HN postings carry no `role_title` until they are scored. A NULL must
    not be coerced to '' and swept into every regex."""
    db_with(tmp_path, [posting("hn:0")])
    c = FakeClient()

    run(tmp_path, "--two-axis", "--title-match", r"design", client=c)

    assert _role_calls(c) == []


def test_an_invalid_regex_is_rejected_loudly(tmp_path, capsys):
    """Failures are loud (CLAUDE.md rule 5). A bad pattern must not silently
    match nothing and report a clean zero-cost run. Asserting the MESSAGE, not
    just SystemExit: argparse exits for many reasons, and a guard that stops
    firing while some unrelated error keeps the test green is no guard."""
    with pytest.raises(SystemExit):
        run(tmp_path, "--two-axis", "--title-match", "design(")
    assert "not a valid regex" in capsys.readouterr().err


def test_title_match_requires_two_axis(tmp_path, capsys):
    """Silently-ignored flags are worse than rejected ones - the comment
    match.py already carries about `--limit`."""
    with pytest.raises(SystemExit):
        run(tmp_path, "--title-match", "design")
    assert "only applies to --two-axis" in capsys.readouterr().err


def test_title_match_composes_with_limit(tmp_path):
    tiered_db(tmp_path)
    c = FakeClient()

    run(tmp_path, "--two-axis", "--title-match", r"design", "--limit", "1",
        client=c)

    assert len(_role_calls(c)) == 1


# --- the un-drop invariant, at the CLI seam -------------------------------
# `tests/test_prefilter_store.py` proves `apply_prefilter({id: None})` clears a
# drop. That is a Store-level fact and it is true. It says NOTHING about
# whether the CLI ever passes a dropped row to `apply_prefilter` - and for a
# long time it did not, because `postings_needing_role_score` filters
# `prefilter_drop IS NULL`, so the audit set excluded exactly the rows the
# audit exists to re-examine. CLAUDE.md, the spec and `--show-dropped`'s own
# footer all promised the opposite.

def test_loosening_titles_py_un_drops_a_row_on_the_next_run(tmp_path, monkeypatch):
    """THE invariant behind calling the prefilter 'loose': a role dropped by
    mistake must come back by loosening a pattern and re-running. The remedy
    `--show-dropped` tells the user to apply must actually work."""
    from jobhunt.matching import titles
    s = db_with(tmp_path, [posting("hn:0", company="A", normalised="a")])
    titled(s, **{"hn:0": "Member of Technical Staff"})
    s.close()

    monkeypatch.setattr(titles, "prefilter_drop", lambda t: "tight: dropped")
    run(tmp_path, "--two-axis", client=FakeClient())
    s1 = Store(tmp_path / "j.sqlite")
    assert [r[0] for r in s1.dropped_postings()] == ["hn:0"]
    s1.close()

    # Loosen: the exact operation c6f7a20 performed on POSITIVE_RE.
    monkeypatch.setattr(titles, "prefilter_drop", lambda t: None)
    c = FakeClient()
    run(tmp_path, "--two-axis", client=c)

    s2 = Store(tmp_path / "j.sqlite")
    assert s2.dropped_postings() == [], "loosening titles.py did not un-drop"
    s2.close()
    assert len(_role_calls(c)) == 1, "un-dropped but still never scored"


def test_a_dropped_row_is_re_audited_even_under_a_tier_filter(tmp_path, monkeypatch):
    """The audit is corpus-wide, so the tier being run must not decide which
    stale drops get reconsidered."""
    from jobhunt.matching import titles
    s = db_with(tmp_path, [posting("hn:0", company="A", normalised="a"),
                           posting("hn:1", company="B", normalised="b")])
    titled(s, **{"hn:0": "Senior Product Designer", "hn:1": "HVAC Technician"})
    s.close()

    monkeypatch.setattr(titles, "prefilter_drop",
                        lambda t: "tight: dropped" if "Technician" in t else None)
    run(tmp_path, "--two-axis", "--title-match", "designer", client=FakeClient())
    s1 = Store(tmp_path / "j.sqlite")
    assert [r[0] for r in s1.dropped_postings()] == ["hn:1"]
    s1.close()

    monkeypatch.setattr(titles, "prefilter_drop", lambda t: None)
    run(tmp_path, "--two-axis", "--title-match", "designer", client=FakeClient())

    s2 = Store(tmp_path / "j.sqlite")
    assert s2.dropped_postings() == [], "a tier filter froze a stale drop"
    s2.close()


# --- a tier must never silently cover less than the user believes ---------

def test_a_valid_but_wrong_pattern_reports_the_denominator(tmp_path, capsys):
    """`desinger` is valid regex and matches nothing. Without a denominator
    that run exits 0 with a clean `runs` row and reads exactly like a tier
    that is already finished."""
    tiered_db(tmp_path)

    run(tmp_path, "--two-axis", "--title-match", "desinger", client=FakeClient())

    out = capsys.readouterr()
    assert "0 of 4 candidate(s) matched" in out.out
    assert "matched none" in out.err


def test_a_matching_pattern_does_not_warn(tmp_path, capsys):
    tiered_db(tmp_path)

    run(tmp_path, "--two-axis", "--title-match", "design", client=FakeClient())

    out = capsys.readouterr()
    assert "2 of 4 candidate(s) matched" in out.out
    assert "matched none" not in out.err


def test_untitled_candidates_are_reported_as_unreachable(tmp_path, capsys):
    """231 of 1,810 live candidates have no role_title - all from HN, where
    slice 2 gotcha 7 says titles are deliberately never parsed. No tier can
    reach them, so a tier-by-tier sweep silently skips them unless it says so."""
    s = db_with(tmp_path, [posting("hn:0"), posting("hn:1", company="B",
                                                    normalised="b")])
    titled(s, **{"hn:0": "Senior Product Designer"})   # hn:1 keeps a NULL title
    s.close()

    run(tmp_path, "--two-axis", "--title-match", "designer", client=FakeClient())

    out = capsys.readouterr().out
    assert "1 candidate(s) have no role title" in out
    assert "without the flag" in out


def test_no_unreachable_note_when_every_candidate_has_a_title(tmp_path, capsys):
    tiered_db(tmp_path)

    run(tmp_path, "--two-axis", "--title-match", "design", client=FakeClient())

    assert "no role title" not in capsys.readouterr().out


def test_an_all_untitled_pool_does_not_blame_the_pattern(tmp_path, capsys):
    """`pool and not postings` fires on an HN-only database, where no tier
    could ever match and the pattern is innocent. Sending the user to check a
    correct pattern is a false lead."""
    db_with(tmp_path, [posting("hn:0"), posting("hn:1", company="B",
                                                normalised="b")])

    run(tmp_path, "--two-axis", "--title-match", "design", client=FakeClient())

    err = capsys.readouterr().err
    assert "no tier can match them" in err
    assert "Check the pattern" not in err


# --- reachability at the CLI seam ----------------------------------------

def test_two_axis_excludes_out_of_area_roles_from_scoring(tmp_path):
    s = db_with(tmp_path, [posting("hn:0", company="A", normalised="a"),
                           posting("hn:1", company="B", normalised="b")])
    titled(s, **{"hn:0": "Product Designer", "hn:1": "Product Designer"})
    s._conn.execute("UPDATE postings SET location='Boston, MA' WHERE id='hn:0'")
    s._conn.execute("UPDATE postings SET location='Austin, TX' WHERE id='hn:1'")
    s._conn.commit()
    s.close()
    c = FakeClient()

    run(tmp_path, "--two-axis", client=c)

    assert len(_role_calls(c)) == 1


def test_widening_the_home_area_un_excludes_at_the_cli(tmp_path, monkeypatch):
    """REVIEW FOCUS 4. The prefilter's C1 bug was exactly this, and its
    Store-level tests passed throughout. A Store-level test is not enough."""
    from jobhunt.matching import geography
    s = db_with(tmp_path, [posting("hn:0", company="A", normalised="a")])
    titled(s, **{"hn:0": "Product Designer"})
    s._conn.execute("UPDATE postings SET location='Austin, TX' WHERE id='hn:0'")
    s._conn.commit()
    s.close()

    run(tmp_path, "--two-axis", client=FakeClient())
    s1 = Store(tmp_path / "j.sqlite")
    assert s1.reach_counts().get("out_of_area") == 1
    s1.close()

    # Widen: Austin is now in the home area.
    monkeypatch.setattr(geography, "reachability",
                        lambda loc, wt, metros, states: "local")
    c = FakeClient()
    run(tmp_path, "--two-axis", client=c)

    s2 = Store(tmp_path / "j.sqlite")
    assert s2.reach_counts().get("out_of_area", 0) == 0, "widening did not un-exclude"
    s2.close()
    assert len(_role_calls(c)) == 1, "un-excluded but never scored"


def test_a_role_with_no_location_is_still_scored(tmp_path):
    """REVIEW FOCUS 1, at the seam."""
    s = db_with(tmp_path, [posting("hn:0")])
    titled(s, **{"hn:0": "Product Designer"})
    s.close()
    c = FakeClient()

    run(tmp_path, "--two-axis", client=c)

    assert len(_role_calls(c)) == 1


def test_show_reach_lists_roles_without_credentials(tmp_path, capsys):
    s = db_with(tmp_path, [posting("hn:0")])
    titled(s, **{"hn:0": "Product Designer"})
    s._conn.execute("UPDATE postings SET location='Austin, TX' WHERE id='hn:0'")
    s._conn.commit()
    s.apply_reachability({"hn:0": "out_of_area"})
    s.close()

    run(tmp_path, "--show-reach", "out_of_area")

    out = capsys.readouterr().out
    assert "Product Designer" in out and "Austin, TX" in out


def test_show_reach_rejects_scoring_flags_instead_of_ignoring_them(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, "--show-reach", "--two-axis")


def test_status_reports_reach_counts(tmp_path, capsys):
    s = db_with(tmp_path, [posting("hn:0")])
    s.apply_reachability({"hn:0": "out_of_area"})
    s.close()

    run(tmp_path, "--status")

    assert "reach: out_of_area=1" in capsys.readouterr().out
