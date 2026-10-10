"""Credential loading from an untracked `.env`.

The SDK resolves `ANTHROPIC_API_KEY` from the process environment only, and a
`launchd` job gets a minimal environment that never reads `~/.zshrc`. These
tests pin the file format and, more importantly, the resolution ORDER - a
loader that silently overrides a real environment variable makes "which key
am I using?" unanswerable.
"""

import pytest

from jobhunt.env import KEY, EnvFileError, load_env, parse_env_text


# --- parse_env_text: pure, no I/O -----------------------------------------

def test_parses_a_simple_assignment():
    assert parse_env_text("ANTHROPIC_API_KEY=sk-ant-abc") == {KEY: "sk-ant-abc"}


def test_ignores_comments_and_blank_lines():
    text = "# a comment\n\nANTHROPIC_API_KEY=sk-ant-abc\n\n   # indented\n"
    assert parse_env_text(text) == {KEY: "sk-ant-abc"}


def test_strips_an_export_prefix():
    # The same file has to be `source`-able from a shell, so `export FOO=bar`
    # is a shape it will really be written in.
    assert parse_env_text("export ANTHROPIC_API_KEY=sk-ant-abc") == {KEY: "sk-ant-abc"}


@pytest.mark.parametrize("quoted", ['"sk-ant-abc"', "'sk-ant-abc'"])
def test_strips_matching_surrounding_quotes(quoted):
    assert parse_env_text(f"ANTHROPIC_API_KEY={quoted}") == {KEY: "sk-ant-abc"}


def test_keeps_an_equals_sign_inside_a_value():
    # Split on the FIRST `=` only. Base64-ish secrets carry padding.
    assert parse_env_text("OTHER=a=b=c") == {"OTHER": "a=b=c"}


def test_handles_crlf_line_endings():
    # The file is meant to be copied between machines.
    assert parse_env_text("ANTHROPIC_API_KEY=sk-ant-abc\r\nOTHER=2\r\n") == {
        KEY: "sk-ant-abc", "OTHER": "2"}


def test_a_line_without_an_equals_sign_is_an_error():
    # Failures are loud (CLAUDE.md rule 5). A typo'd secrets file must not
    # quietly resolve to "no credentials".
    with pytest.raises(EnvFileError):
        parse_env_text("ANTHROPIC_API_KEY sk-ant-abc")


def test_an_empty_key_is_an_error():
    with pytest.raises(EnvFileError):
        parse_env_text("=sk-ant-abc")


# --- load_env: resolution order -------------------------------------------

def _write(path, text, mode=0o600):
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def test_an_existing_environment_variable_wins_over_the_file(tmp_path):
    env_file = _write(tmp_path / ".env", f"{KEY}=from-file")
    environ = {KEY: "from-environment"}

    source = load_env(environ=environ, repo_env=env_file)

    assert environ[KEY] == "from-environment"
    assert source == "environment"


def test_loads_the_repo_env_file_when_the_variable_is_unset(tmp_path):
    env_file = _write(tmp_path / ".env", f"{KEY}=from-file")
    environ = {}

    source = load_env(environ=environ, repo_env=env_file)

    assert environ[KEY] == "from-file"
    assert source == str(env_file)


def test_jobhunt_env_file_takes_precedence_over_the_repo_env(tmp_path):
    repo_env = _write(tmp_path / ".env", f"{KEY}=from-repo")
    elsewhere = _write(tmp_path / "secure.env", f"{KEY}=from-elsewhere")
    environ = {"JOBHUNT_ENV_FILE": str(elsewhere)}

    source = load_env(environ=environ, repo_env=repo_env)

    assert environ[KEY] == "from-elsewhere"
    assert source == str(elsewhere)


def test_a_missing_jobhunt_env_file_is_an_error(tmp_path):
    # Pointed at a file that is not there = misconfiguration, NOT a reason to
    # fall back to the repo `.env` and use a different key than intended.
    repo_env = _write(tmp_path / ".env", f"{KEY}=from-repo")
    environ = {"JOBHUNT_ENV_FILE": str(tmp_path / "absent.env")}

    with pytest.raises(EnvFileError, match="absent.env"):
        load_env(environ=environ, repo_env=repo_env)


def test_reports_none_when_no_file_and_no_variable(tmp_path):
    # `--print-prompt`, `--show` and `--status` must keep working with no
    # credentials at all, so this is silent, not an error.
    environ = {}

    assert load_env(environ=environ, repo_env=tmp_path / "absent.env") == "none"
    assert KEY not in environ


def test_a_file_without_the_key_reports_none_but_still_loads_other_vars(tmp_path):
    env_file = _write(tmp_path / ".env", "OTHER=2")
    environ = {}

    assert load_env(environ=environ, repo_env=env_file) == "none"
    assert environ["OTHER"] == "2"


def test_a_group_or_world_readable_file_warns(tmp_path, capsys):
    env_file = _write(tmp_path / ".env", f"{KEY}=from-file", mode=0o644)

    load_env(environ={}, repo_env=env_file)

    assert "permission" in capsys.readouterr().err.lower()


def test_a_private_file_does_not_warn(tmp_path, capsys):
    env_file = _write(tmp_path / ".env", f"{KEY}=from-file", mode=0o600)

    load_env(environ={}, repo_env=env_file)

    assert capsys.readouterr().err == ""


def test_the_warning_never_contains_the_secret(tmp_path, capsys):
    env_file = _write(tmp_path / ".env", f"{KEY}=sk-ant-supersecret", mode=0o644)

    load_env(environ={}, repo_env=env_file)

    captured = capsys.readouterr()
    assert "sk-ant-supersecret" not in captured.err + captured.out


def test_does_not_override_other_variables_already_set(tmp_path):
    env_file = _write(tmp_path / ".env", "OTHER=from-file")
    environ = {"OTHER": "from-environment"}

    load_env(environ=environ, repo_env=env_file)

    assert environ["OTHER"] == "from-environment"


def test_an_empty_string_variable_counts_as_unset(tmp_path):
    # `ANTHROPIC_API_KEY=` in a shell profile is the classic way to end up
    # with an empty credential and a baffling 401.
    env_file = _write(tmp_path / ".env", f"{KEY}=from-file")
    environ = {KEY: ""}

    assert load_env(environ=environ, repo_env=env_file) == str(env_file)
    assert environ[KEY] == "from-file"


def test_the_default_repo_env_path_is_the_repo_root_dotenv():
    # `load_env()` with no `repo_env` must resolve somewhere real; a typo here
    # would only surface at runtime on a machine that has a `.env`.
    from jobhunt import config
    assert config.ENV_PATH == config.REPO_ROOT / ".env"


def test_load_env_with_no_arguments_uses_the_config_path(monkeypatch, tmp_path):
    from jobhunt import config
    env_file = _write(tmp_path / ".env", f"{KEY}=from-default-path")
    monkeypatch.setattr(config, "ENV_PATH", env_file)
    environ = {}

    assert load_env(environ=environ) == str(env_file)
    assert environ[KEY] == "from-default-path"


def test_the_env_file_is_gitignored():
    """The repo's history was re-initialised once over committed sensitive
    data. `.env` holds a live API key, so ask git itself rather than
    string-matching `.gitignore`."""
    import subprocess

    from jobhunt import config

    result = subprocess.run(
        ["git", "check-ignore", "-q", str(config.REPO_ROOT / ".env")],
        cwd=config.REPO_ROOT, capture_output=True)
    assert result.returncode == 0, ".env is NOT gitignored"
