from pathlib import Path

from jobhunt.matching.evaluate import (AGREEMENT_BAR, evaluate, load_labels,
                                       passed, render_report)
from jobhunt.matching.models import Label, MatchVerdict


def v(bucket, signal="explicit"):
    return MatchVerdict(role_title="R", discipline="design", hybrid_signal=signal,
                        reason="r", concern="a four-person team may not have a design practice", fit_score=50, bucket=bucket)


def labels(*pairs):
    return [Label(id=f"hn:{i}", company=f"C{i}", verdict=b, criterion="x")
            for i, b in enumerate(pairs, 1)]


def test_perfect_agreement():
    ls = labels("strong", "maybe", "no")
    got = {l.id: v(l.verdict) for l in ls}
    r = evaluate(ls, got)
    assert r.total == 3 and r.exact == 3 and r.agreement == 1.0
    assert r.disagreements == [] and r.severe_count == 0


def test_adjacent_disagreement_is_not_severe():
    ls = labels("strong")
    r = evaluate(ls, {"hn:1": v("maybe")})
    assert r.exact == 0 and len(r.disagreements) == 1
    assert r.disagreements[0].severe is False


def test_strong_to_no_inversion_is_severe():
    ls = labels("strong")
    r = evaluate(ls, {"hn:1": v("no")})
    assert r.severe_count == 1
    ls2 = labels("no")
    assert evaluate(ls2, {"hn:1": v("strong")}).severe_count == 1


def test_missing_verdict_counts_as_a_disagreement_not_a_crash():
    ls = labels("no", "strong")
    r = evaluate(ls, {"hn:1": v("no")})     # hn:2 never scored
    assert r.total == 2 and r.exact == 1
    assert any(d.id == "hn:2" for d in r.disagreements)


def test_confusion_matrix_counts_every_pair():
    ls = labels("no", "no", "strong")
    r = evaluate(ls, {"hn:1": v("no"), "hn:2": v("maybe"), "hn:3": v("strong")})
    assert r.confusion["no"]["no"] == 1
    assert r.confusion["no"]["maybe"] == 1
    assert r.confusion["strong"]["strong"] == 1
    assert sum(sum(row.values()) for row in r.confusion.values()) == 3


def test_agreement_is_a_fraction_not_a_percentage():
    r = evaluate(labels("no", "no"), {"hn:1": v("no"), "hn:2": v("strong")})
    assert r.agreement == 0.5


def test_report_states_the_bar_and_whether_it_passed():
    r = evaluate(labels("no"), {"hn:1": v("no")})
    out = render_report(r)
    assert "60" in out and "PASS" in out.upper()
    bad = evaluate(labels("strong"), {"hn:1": v("no")})
    assert "FAIL" in render_report(bad).upper()


def test_severe_disagreement_fails_even_at_high_agreement():
    # 3 of 4 exact = 75% agreement, but one inversion. Must fail.
    ls = labels("no", "no", "no", "strong")
    got = {"hn:1": v("no"), "hn:2": v("no"), "hn:3": v("no"), "hn:4": v("no")}
    r = evaluate(ls, got)
    assert r.agreement == 0.75 and r.severe_count == 1
    assert "FAIL" in render_report(r).upper()


def test_loads_the_real_frozen_labels():
    ls = load_labels(Path("tests/fixtures/matching/eval_labels.yaml"))
    assert len(ls) == 25
    from collections import Counter
    assert Counter(l.verdict for l in ls) == {"no": 20, "maybe": 4, "strong": 1}
    # YAML 1.1 parses a bare `no` as False; the fixture quotes them.
    assert all(isinstance(l.verdict, str) for l in ls)


def test_agreement_bar_is_sixty_percent():
    assert AGREEMENT_BAR == 0.60


def test_an_unscored_label_is_severe_not_a_free_pass():
    """A per-posting API failure is a NORMAL condition (spec 9), so without
    this a failure on the one `strong` label reported 24/25 = 96%, zero
    severe, PASS - a pass earned by never attempting the only question that
    mattered."""
    ls = labels("strong", "no", "no", "no")
    got = {l.id: v("no", "none") for l in ls[1:]}      # the `strong` one is missing
    r = evaluate(ls, got)
    assert r.unscored == 1
    assert r.severe_count == 1
    assert not passed(r)
    assert "FAIL" in render_report(r).upper()


def test_a_prompt_that_says_no_to_everything_fails_the_balance_bar():
    """The hole exact agreement cannot see. 20 of the 25 real labels are `no`,
    so answering `no` to everything scores 80%; answering `maybe` to the one
    `strong` posting scores 76% with ZERO severe inversions. The rubric is
    deliberately loaded toward `no`, so this is the likelier failure."""
    ls = labels("strong", "maybe", "maybe", "maybe", "maybe",
                *(["no"] * 20))
    got = {l.id: v("no", "none") for l in ls}
    got[ls[0].id] = v("maybe", "contextual")           # dodges the severe check
    r = evaluate(ls, got)
    assert r.severe_count == 0
    assert r.agreement >= AGREEMENT_BAR                # 20/25 = 80%, past the bar
    assert r.positive_hit == 1 and r.positive_total == 5
    assert not passed(r), "a prompt that can never surface a job must not pass"
    assert "balance bar" in render_report(r)


def test_the_balance_bar_is_vacuous_with_no_positive_labels():
    ls = labels("no", "no")
    r = evaluate(ls, {l.id: v("no", "none") for l in ls})
    assert r.positive_total == 0
    assert passed(r)
    assert "n/a" in render_report(r)
