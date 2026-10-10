"""The model boundary. The client is injected; no test makes a live call."""

import pytest

from jobhunt.generation.models import ChosenBullet, CoverLetter, ResumeSelection
from jobhunt.generation.writer import (Writer, accept_selection,
                                       check_citations)

ELIGIBLE = {
    "b1": "Led an AI triage tool for Fufo Vurez technicians through rollout.",
    "b2": "Shipped the first design system at the company.",
}


class FakeBlock:
    def __init__(self, parsed): self.type = "text"; self.parsed_output = parsed
class FakeMsg:
    def __init__(self, parsed):
        self.content = [FakeBlock(parsed)]; self.stop_reason = "end_turn"
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5,
                                    "cache_read_input_tokens": 0})()
class FakeMessages:
    def __init__(self, parsed): self._p = parsed; self.kwargs = None
    def parse(self, **kw):
        self.kwargs = kw
        return FakeMsg(self._p)
class FakeClient:
    def __init__(self, parsed): self.messages = FakeMessages(parsed)


def test_select_bullets_returns_the_parsed_selection():
    sel = ResumeSelection(bullets=[ChosenBullet(
        bullet_id="b1", rewritten=ELIGIBLE["b1"], answers="AI kapasu")])
    got = Writer(FakeClient(sel)).select_bullets("SYS", "USER")
    assert got.bullets[0].bullet_id == "b1"


def test_the_system_block_is_cached():
    sel = ResumeSelection(bullets=[])
    c = FakeClient(sel)
    Writer(c).select_bullets("SYS", "USER")
    assert c.messages.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_an_accepted_re_angle_uses_the_rewrite():
    ok = "Led an AI triage tool for Fufo Vurez technicians through the rollout."
    sel = ResumeSelection(bullets=[ChosenBullet(
        bullet_id="b1", rewritten=ok, answers="x")])
    out = accept_selection(sel, ELIGIBLE)
    assert out[0].text == ok and out[0].refused_reason is None


def test_an_invented_number_falls_back_to_the_original():
    """Refused, never silently corrected, and never dropped."""
    bad = ELIGIBLE["b1"].replace("rollout.", "rollout, cutting errors 40%.")
    sel = ResumeSelection(bullets=[ChosenBullet(
        bullet_id="b1", rewritten=bad, answers="x")])
    out = accept_selection(sel, ELIGIBLE)
    assert out[0].text == ELIGIBLE["b1"]
    assert "40" in out[0].refused_reason


def test_a_bullet_id_outside_the_eligible_set_is_refused():
    """REVIEW-CRITICAL. The model could return an untagged bullet's id, or
    invent one. Either would put unreviewed prose into a document."""
    sel = ResumeSelection(bullets=[ChosenBullet(
        bullet_id="b99", rewritten="Anything at all.", answers="x")])
    out = accept_selection(sel, ELIGIBLE)
    assert out == [] or all(a.bullet_id != "b99" for a in out)


def test_a_duplicate_bullet_id_appears_once():
    sel = ResumeSelection(bullets=[
        ChosenBullet(bullet_id="b1", rewritten=ELIGIBLE["b1"], answers="x"),
        ChosenBullet(bullet_id="b1", rewritten=ELIGIBLE["b1"], answers="y")])
    assert len(accept_selection(sel, ELIGIBLE)) == 1


def test_a_cover_letter_citing_an_unknown_case_study_is_reported():
    """REVIEW-CRITICAL. Citing work that is not in case_studies.yaml is a
    fabrication, the same class of error as an invented number."""
    letter = CoverLetter(paragraphs=["Hello."],
                         cited_case_studies=["Fufo Vurez Triage Tool",
                                             "A Study I Made Up"])
    assert check_citations(letter, {"Fufo Vurez Triage Tool"}) == \
        ["A Study I Made Up"]


def test_a_cover_letter_citing_only_known_studies_is_clean():
    letter = CoverLetter(paragraphs=["Hello."],
                         cited_case_studies=["Fufo Vurez Triage Tool"])
    assert check_citations(letter, {"Fufo Vurez Triage Tool"}) == []


def test_an_empty_paragraph_list_is_rejected_by_the_model():
    with pytest.raises(Exception):
        CoverLetter(paragraphs=[], cited_case_studies=[])
