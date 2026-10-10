import json
from pathlib import Path

import anthropic
import httpx2
import pytest

from jobhunt.discovery.models import Posting
from jobhunt.discovery.store import Store
from jobhunt.matching.match import main
from jobhunt.matching.models import MatchVerdict


def _authentication_error():
    # anthropic 1.8.0's `AuthenticationError.__init__` requires a real
    # `response`; constructed via httpx2 (the SDK's internal http lib, a
    # separate installed package from `httpx`) exactly as the SDK itself
    # would build one from a 401.
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx2.Response(401, request=req)
    return anthropic.AuthenticationError("invalid x-api-key", response=resp, body=None)

PROFILE = Path("tests/fixtures/profile_golden.json")
pytestmark = pytest.mark.skipif(
    not PROFILE.is_file(),
    reason="run `python -m jobhunt.ingest` first to generate data/profile.json")


def posting(pid, text="Acme | Senior Design Engineer | Remote US | Full-time"):
    return Posting(id=pid, source="hn", source_thread_id="9", company="Acme",
                   company_normalised="acme", raw_text=text, apply_url=None,
                   is_remote=True, geo_signal="us", posted_at="2026-09-01",
                   first_seen="x", last_seen="x", status="new")


def verdict(bucket="strong", signal="explicit"):
    return MatchVerdict(role_title="Senior Design Engineer", discipline="design",
                        hybrid_signal=signal, reason="clear hybrid role",
                        concern="a four-person team may not have a design practice", fit_score=82, bucket=bucket)


class FakeBlock:
    def __init__(self, p): self.type = "text"; self.parsed_output = p
class FakeMsg:
    def __init__(self, p):
        self.content = [FakeBlock(p)]; self.stop_reason = "end_turn"
        self.stop_details = None
        self.usage = type("U", (), {"cache_read_input_tokens": 9000,
                                    "input_tokens": 60, "output_tokens": 130})()
class FakeMessages:
    def __init__(self, v): self._v = v; self.calls = 0
    def parse(self, **kw): self.calls += 1; return FakeMsg(self._v)
class FakeClient:
    def __init__(self, v=None): self.messages = FakeMessages(v or verdict())


def db_with(tmp_path, n=3):
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    for i in range(1, n + 1): s.upsert_posting(posting(f"hn:{i}"))
    return s


def run(tmp_path, *extra, client=None):
    return main(["--db", str(tmp_path / "j.sqlite"), *extra],
                client=client if client is not None else FakeClient())


def test_scores_every_unscored_posting(tmp_path, capsys):
    db_with(tmp_path, 3)
    assert run(tmp_path) == 0
    s = Store(tmp_path / "j.sqlite")
    assert len(s.unscored()) == 0 and len(s.scored()) == 3
    assert s.verdict_of("hn:1").bucket == "strong"


def test_limit_caps_the_number_scored(tmp_path):
    db_with(tmp_path, 3)
    c = FakeClient()
    run(tmp_path, "--limit", "1", client=c)
    assert c.messages.calls == 1
    assert len(Store(tmp_path / "j.sqlite").unscored()) == 2


def test_print_prompt_makes_no_api_call_and_needs_no_client(tmp_path, capsys):
    db_with(tmp_path, 1)
    c = FakeClient()
    assert run(tmp_path, "--print-prompt", client=c) == 0
    assert c.messages.calls == 0
    out = capsys.readouterr().out
    assert "specific candidate" in out and "spans disciplines" in out


def test_a_failing_posting_does_not_fail_the_run(tmp_path, capsys):
    db_with(tmp_path, 2)
    class Boom(FakeMessages):
        def parse(self, **kw):
            self.calls += 1
            if self.calls == 1: raise RuntimeError("upstream exploded")
            return FakeMsg(verdict())
    c = FakeClient(); c.messages = Boom(verdict())
    assert run(tmp_path, client=c) == 0
    assert len(Store(tmp_path / "j.sqlite").scored()) == 1
    assert "upstream exploded" in capsys.readouterr().err


def test_rescore_without_all_is_a_noop_when_nothing_is_stale(tmp_path, capsys):
    db_with(tmp_path, 1); run(tmp_path)
    capsys.readouterr()
    c = FakeClient()
    assert run(tmp_path, "--rescore", client=c) == 0
    assert c.messages.calls == 0
    assert "nothing" in capsys.readouterr().out.lower()


def test_rescore_all_rescores_everything(tmp_path):
    db_with(tmp_path, 2); run(tmp_path)
    c = FakeClient()
    run(tmp_path, "--rescore", "--all", client=c)
    assert c.messages.calls == 2


def test_show_lists_stored_verdicts_with_reason_and_concern(tmp_path, capsys):
    db_with(tmp_path, 1); run(tmp_path); capsys.readouterr()
    run(tmp_path, "--show")
    out = capsys.readouterr().out
    assert "Senior Design Engineer" in out
    assert "clear hybrid role" in out and "design practice" in out


def test_status_reports_counts(tmp_path, capsys):
    db_with(tmp_path, 2); run(tmp_path); capsys.readouterr()
    run(tmp_path, "--status")
    out = capsys.readouterr().out
    assert "strong" in out and "2" in out


def test_cache_read_tokens_are_reported(tmp_path, capsys):
    db_with(tmp_path, 1); run(tmp_path)
    assert "9000" in capsys.readouterr().out


def test_eval_returns_one_when_the_bar_is_missed(tmp_path, capsys):
    """Every label scored `strong` against 20 human `no`s must fail loudly."""
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    import yaml
    labels = yaml.safe_load(Path("tests/fixtures/matching/eval_labels.yaml")
                            .read_text(encoding="utf-8"))["labels"]
    for row in labels:
        s.upsert_posting(posting(row["id"]))
    rc = run(tmp_path, "--eval", client=FakeClient(verdict("strong")))
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out.upper() and "SEVERE" in out.upper()


def test_eval_returns_zero_when_the_model_matches_the_humans(tmp_path):
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    import yaml
    labels = yaml.safe_load(Path("tests/fixtures/matching/eval_labels.yaml")
                            .read_text(encoding="utf-8"))["labels"]
    for row in labels:
        s.upsert_posting(posting(row["id"]))

    # The planted `Mimic` class from the plan (half-written, referenced an
    # undefined `self._order`) has been deleted. `InOrder` returns each human
    # verdict in scoring order, which only works because `--eval` scores the
    # labelled postings in label-file order (see match.py's `_cmd_eval`).
    order = [row["verdict"] for row in labels]
    class InOrder(FakeMessages):
        def __init__(self): self.calls = 0
        def parse(self, **kw):
            b = order[self.calls]; self.calls += 1
            sig = "explicit" if b == "strong" else "none"
            return FakeMsg(verdict(b, sig))
    c = FakeClient(); c.messages = InOrder()
    assert run(tmp_path, "--eval", client=c) == 0


def test_authentication_failure_aborts_the_run_after_the_first_posting(tmp_path, capsys):
    db_with(tmp_path, 3)
    err = _authentication_error()
    class Unauthorized(FakeMessages):
        def parse(self, **kw):
            self.calls += 1
            raise err
    c = FakeClient(); c.messages = Unauthorized(verdict())
    rc = run(tmp_path, client=c)
    assert rc != 0
    # Every remaining posting would fail identically, so the run must not
    # keep retrying them one by one.
    assert c.messages.calls == 1
    err_out = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err_out
    assert "ant auth login" in err_out


def test_authentication_failure_is_recorded_on_the_run_row(tmp_path):
    db_with(tmp_path, 1)
    err = _authentication_error()
    class Unauthorized(FakeMessages):
        def parse(self, **kw):
            self.calls += 1
            raise err
    c = FakeClient(); c.messages = Unauthorized(verdict())
    run(tmp_path, client=c)
    # kind="match": a matching run must NOT be visible to last_run() with its
    # discovery default, or it answers discovery's heartbeat question.
    last = Store(tmp_path / "j.sqlite").last_run(Store.MATCH)
    assert last is not None
    assert last.error is not None
    assert "ANTHROPIC_API_KEY" in last.error


def _seeded_label_db(tmp_path):
    import yaml
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    labels = yaml.safe_load(Path("tests/fixtures/matching/eval_labels.yaml")
                            .read_text(encoding="utf-8"))["labels"]
    for row in labels:
        s.upsert_posting(posting(row["id"]))
    return s, labels


def test_eval_reuses_stored_verdicts_and_says_how_many(tmp_path, capsys):
    _seeded_label_db(tmp_path)
    first = FakeClient(verdict("strong"))
    run(tmp_path, "--eval", client=first)
    assert first.messages.calls == 25
    capsys.readouterr()

    second = FakeClient(verdict("strong"))
    run(tmp_path, "--eval", client=second)
    assert second.messages.calls == 0, "nothing changed, so nothing should be re-paid"
    out = capsys.readouterr().out
    # Printed unconditionally. Silence read as "nothing cost anything", which
    # is indistinguishable from "this entire report is recycled".
    assert "reused: 25" in out and "to score: 0" in out


def test_eval_rescore_forces_a_fresh_score(tmp_path, capsys):
    """`--eval --rescore` used to be silently ignored, so the one command a
    user would reach for after editing the rubric did nothing at all."""
    _seeded_label_db(tmp_path)
    run(tmp_path, "--eval", client=FakeClient(verdict("strong")))
    capsys.readouterr()
    c = FakeClient(verdict("strong"))
    run(tmp_path, "--eval", "--rescore", client=c)
    assert c.messages.calls == 25
    assert "reused: 0" in capsys.readouterr().out


def test_eval_does_not_reuse_verdicts_from_another_model(tmp_path, capsys):
    """Staleness was keyed on the profile alone, so `--eval --model X` reported
    the PREVIOUS model's numbers without making a single call."""
    _seeded_label_db(tmp_path)
    run(tmp_path, "--eval", client=FakeClient(verdict("strong")))
    capsys.readouterr()
    c = FakeClient(verdict("strong"))
    run(tmp_path, "--eval", "--model", "claude-haiku-4-5", client=c)
    assert c.messages.calls == 25


def test_a_stale_verdict_is_reported_with_its_remedy(tmp_path, capsys):
    """Spec 9. Without it the user edits the rubric, re-runs, and nothing says
    that every stored verdict now describes a prompt that no longer exists."""
    db_with(tmp_path, 2)
    run(tmp_path); capsys.readouterr()
    s = Store(tmp_path / "j.sqlite")
    s._conn.execute("UPDATE postings SET match_profile_sha = 'OLD'")
    s._conn.commit()

    run(tmp_path, "--status")
    cap = capsys.readouterr()
    assert "stale verdicts: 2" in cap.out
    assert "--rescore" in cap.err


def test_repeated_failures_with_the_same_root_cause_abort_the_run(tmp_path, capsys):
    """The generic guard behind the auth detection. If the SDK's private
    "authentication method" wording ever changes, this is what still stops 132
    cryptic failures and a "scored 0 posting(s)" exit 0."""
    db_with(tmp_path, 10)
    class AlwaysBroken(FakeMessages):
        def parse(self, **kw):
            self.calls += 1
            raise anthropic.NotFoundError(
                "model not found", response=httpx2.Response(
                    404, request=httpx2.Request("POST", "https://x")), body=None)
    c = FakeClient(); c.messages = AlwaysBroken(verdict())
    rc = run(tmp_path, client=c)
    assert rc == 1
    assert c.messages.calls == 3, "must stop, not work through all 10"
    cap = capsys.readouterr()
    assert "consecutive" in cap.err and "NotFoundError" in cap.err
    # The abort path must still report what it did manage to do.
    assert "scored 0 posting(s), 3 failed" in cap.out


def test_unrelated_transient_failures_do_not_abort_the_run(tmp_path, capsys):
    """The streak keys on the ROOT CAUSE, not on the raised type. `Scorer`
    wraps everything into `ScoreError`, so keying on the raised type made
    "three failures of the same type" mean "three failures of ANY cause" -
    and three unrelated transient faults in a row killed a run that spec 9
    says must continue ("131 verdicts beat zero").

    Rate limits and connection errors are exempt outright: the SDK has
    already retried them before Scorer ever sees one.
    """
    db_with(tmp_path, 6)
    req = httpx2.Request("POST", "https://x")
    faults = [
        anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=req), body=None),
        anthropic.APIConnectionError(request=req),
        anthropic.InternalServerError("oops", response=httpx2.Response(500, request=req), body=None),
    ]
    class Flaky(FakeMessages):
        def parse(self, **kw):
            self.calls += 1
            if self.calls <= 3:
                raise faults[self.calls - 1]
            return FakeMsg(verdict())
    c = FakeClient(); c.messages = Flaky(verdict())
    assert run(tmp_path, client=c) == 0
    assert c.messages.calls == 6, "must work through all six, not abort at three"
    assert "scored 3 posting(s), 3 failed" in capsys.readouterr().out


def test_status_survives_an_unreadable_profile(tmp_path, capsys):
    """`prompt_sha` renders the whole prompt, so an incomplete profile now
    raises where the old profile-dict hash did not. `--status` is read-only
    and must still report the counts above it."""
    db_with(tmp_path, 1)
    broken = tmp_path / "broken.json"
    broken.write_text('{"summaries": {"design": "d", "eng": "e"}}', encoding="utf-8")
    rc = main(["--db", str(tmp_path / "j.sqlite"), "--profile", str(broken),
               "--status"], client=FakeClient())
    assert rc == 0
    out = capsys.readouterr().out
    assert "unscored (HN, single-axis) remaining: 1" in out
    assert "stale verdicts: unknown" in out and "jobhunt.ingest" in out
    assert "ATS roles awaiting a role verdict: unknown" in out


def test_status_survives_a_missing_profile_file(tmp_path, capsys):
    """`--status` must keep working with no credentials AND no profile at all
    - not just a present-but-broken one. Both the staleness line and the new
    ATS role-verdict backlog line need `profile.json` to compute their
    fingerprint, and neither may crash the command that is supposed to be the
    one thing that always works."""
    db_with(tmp_path, 1)
    missing = tmp_path / "does-not-exist.json"
    rc = main(["--db", str(tmp_path / "j.sqlite"), "--profile", str(missing),
               "--status"], client=FakeClient())
    assert rc == 0
    out = capsys.readouterr().out
    assert "unscored (HN, single-axis) remaining: 1" in out
    assert "stale verdicts: unknown" in out
    assert "ATS roles awaiting a role verdict: unknown" in out


def test_status_reports_ats_roles_awaiting_a_role_verdict(tmp_path, capsys):
    """The old unlabelled `unscored remaining:` line under-reports once
    `unscored()` is filtered to `source = 'hn'`: a user polling ~1,039 ATS
    roles saw a small HN-only number and concluded there was nothing left to
    score. `--status` must also report the ATS/role-fit backlog that
    `postings_needing_role_score` already knows about."""
    from jobhunt.discovery.models import AtsRole
    s = Store(tmp_path / "j.sqlite")
    s.init_schema()
    s.upsert_ats_role(
        AtsRole(id="ashby:acme:1", provider="ashby", token="acme",
                company="Acme", title="Staff Product Designer",
                body="duties " * 60, location="Remote",
                workplace_type="remote", department="Design",
                apply_url="https://x/y", posted_at="2026-09-20"),
        now="2026-09-29")

    run(tmp_path, "--status")

    out = capsys.readouterr().out
    assert "ATS roles awaiting a role verdict: 1" in out


def test_a_partial_run_reports_how_many_failed(tmp_path, capsys):
    """"scored 2 posting(s)" out of 3 attempted is a silent partial success."""
    db_with(tmp_path, 3)
    class OneBad(FakeMessages):
        def parse(self, **kw):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("transient")
            return FakeMsg(verdict())
    c = FakeClient(); c.messages = OneBad(verdict())
    assert run(tmp_path, client=c) == 0
    out = capsys.readouterr().out
    assert "scored 2 posting(s)" in out and "1 failed" in out


@pytest.mark.parametrize("argv", [
    ["--all"],                        # means nothing without --rescore
    ["--limit", "5", "--rescore"],    # looked like a cheap partial re-score
    ["--limit", "5", "--eval"],       # the label set is the limit
    ["--eval", "--all"],              # --eval --rescore already means all 25
])
def test_silently_ignored_flag_combinations_are_rejected(tmp_path, argv):
    db_with(tmp_path, 1)
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, *argv)
    assert exc.value.code == 2


def test_show_rejects_an_unknown_bucket(tmp_path):
    db_with(tmp_path, 1)
    with pytest.raises(SystemExit):
        run(tmp_path, "--show", "bogus")


# --- credentials ----------------------------------------------------------

def _isolate_credentials(monkeypatch, tmp_path, contents=None):
    """Point the loader at a temp `.env` and clear any inherited key, so these
    tests do not depend on whether this machine happens to have credentials."""
    from jobhunt import config, env
    monkeypatch.delenv(env.KEY, raising=False)
    monkeypatch.delenv(env.ENV_FILE_VAR, raising=False)
    path = tmp_path / ".env"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o600)
    monkeypatch.setattr(config, "ENV_PATH", path)
    return path


def test_status_reports_where_credentials_came_from(monkeypatch, tmp_path, capsys):
    env_file = _isolate_credentials(monkeypatch, tmp_path,
                                    "ANTHROPIC_API_KEY=sk-ant-fromfile\n")
    db_with(tmp_path, 1)

    run(tmp_path, "--status")

    assert f"credentials: {env_file}" in capsys.readouterr().out


def test_status_reports_none_when_there_are_no_credentials(monkeypatch, tmp_path,
                                                           capsys):
    _isolate_credentials(monkeypatch, tmp_path)
    db_with(tmp_path, 1)

    run(tmp_path, "--status")

    assert "credentials: none" in capsys.readouterr().out


def test_status_never_prints_the_key_itself(monkeypatch, tmp_path, capsys):
    # The whole point of reporting a SOURCE rather than a value.
    _isolate_credentials(monkeypatch, tmp_path,
                         "ANTHROPIC_API_KEY=sk-ant-supersecret\n")
    db_with(tmp_path, 1)

    run(tmp_path, "--status")

    captured = capsys.readouterr()
    assert "sk-ant-supersecret" not in captured.out + captured.err


def test_the_cli_puts_the_key_into_the_environment(monkeypatch, tmp_path):
    # The SDK reads os.environ and nothing else, so loading the file is only
    # useful if the value actually lands there before a client is built.
    import os

    from jobhunt import env
    _isolate_credentials(monkeypatch, tmp_path,
                         "ANTHROPIC_API_KEY=sk-ant-fromfile\n")
    db_with(tmp_path, 1)

    run(tmp_path, "--status")

    assert os.environ[env.KEY] == "sk-ant-fromfile"


def test_a_broken_env_file_is_one_message_not_a_traceback(monkeypatch, tmp_path,
                                                          capsys):
    """A typo in a config path is a user error, not a crash. Other fatal
    conditions already print one line and exit 1; this must match."""
    from jobhunt import env
    _isolate_credentials(monkeypatch, tmp_path)
    monkeypatch.setenv(env.ENV_FILE_VAR, str(tmp_path / "absent.env"))
    db_with(tmp_path, 1)

    assert run(tmp_path, "--status") == 1

    captured = capsys.readouterr()
    assert "absent.env" in captured.err
    assert "Traceback" not in captured.err
