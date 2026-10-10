"""The anti-fabrication guards. THE load-bearing part of slice 4.

A fabricated metric on a resume is the one failure here that can genuinely
harm Sam, so these checks exist to make "select and re-angle, never
generate from scratch" mechanical rather than aspirational.
"""

import pytest

from jobhunt.generation.guards import (MIN_TOKEN_OVERLAP, check_reangle,
                                       new_numerals, new_proper_nouns,
                                       token_overlap)

ORIGINAL = ("Led an AI triage tool for Fufo Vurez technicians from research "
            "through rollout, reducing diagnostic errors and repeat visits.")


def test_an_invented_number_is_caught():
    """The failure that matters. The original claims no figure; the rewrite
    invents one."""
    bad = ORIGINAL.replace("repeat visits", "repeat visits by 40%")
    assert new_numerals(ORIGINAL, bad) == ["40"]


def test_a_number_present_in_the_original_is_fine():
    orig = "Improved technician efficiency by 35%."
    assert new_numerals(orig, "Lifted technician efficiency 35%.") == []


def test_an_invented_employer_is_caught():
    bad = ORIGINAL.replace("Fufo Vurez", "Google")
    assert "Google" in new_proper_nouns(ORIGINAL, bad)


def test_a_sentence_initial_capital_is_not_a_new_proper_noun():
    """Otherwise every re-angle that starts a sentence with a different word
    is refused."""
    assert new_proper_nouns("led the rollout.", "Rollout was led by him.") == []


def test_proper_noun_comparison_is_case_folded():
    assert new_proper_nouns("worked at tidewater", "Worked at Tidewater") == []


def test_identical_text_has_full_overlap():
    assert token_overlap(ORIGINAL, ORIGINAL) == pytest.approx(1.0)


def test_unrelated_text_has_near_zero_overlap():
    assert token_overlap(ORIGINAL, "Baked a cake for the office party.") < 0.2


def test_stopwords_do_not_prop_up_the_overlap():
    """'the of and to a' sharing would otherwise make any two sentences look
    similar."""
    assert token_overlap("the quick brown fox", "the slow green turtle") < 0.3


def test_the_floor_is_a_named_constant_at_the_specified_value():
    assert MIN_TOKEN_OVERLAP == 0.5


def test_check_reangle_accepts_a_genuine_re_angle():
    ok = ("Led an AI triage tool for Fufo Vurez technicians through research "
          "and rollout, cutting diagnostic errors and repeat visits.")
    assert check_reangle(ORIGINAL, ok) is None


def test_check_reangle_refuses_an_invented_number():
    bad = ORIGINAL.replace("repeat visits", "repeat visits by 40%")
    reason = check_reangle(ORIGINAL, bad)
    assert reason and "40" in reason


def test_check_reangle_refuses_a_rewrite_below_the_floor():
    reason = check_reangle(ORIGINAL, "Fixed computers at a shop.")
    assert reason and "overlap" in reason.lower()


def test_check_reangle_accepts_the_text_unchanged():
    assert check_reangle(ORIGINAL, ORIGINAL) is None
