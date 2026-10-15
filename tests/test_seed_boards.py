"""Seeding hand-curated boards. The registry is otherwise fed only by HN token
harvesting, which cannot reach a company that never posted there."""
from jobhunt.discovery.store import Store
from jobhunt.discovery.seed import load_seed, seed_boards


def test_the_shipped_seed_file_parses_and_is_non_empty():
    boards = load_seed()
    assert boards
    from jobhunt.discovery.seed import SEED_PROVIDERS
    assert {b.provider for b in boards} <= set(SEED_PROVIDERS)


def test_seeding_registers_every_board(tmp_path):
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    added = seed_boards(s, load_seed())
    assert added == len(load_seed())
    assert len(s.companies_with_boards()) == added
    s.close()


def test_seeding_is_idempotent(tmp_path):
    """Re-running must not duplicate or clobber a harvested token."""
    s = Store(tmp_path / "j.sqlite"); s.init_schema()
    seed_boards(s, load_seed())
    before = len(s.companies_with_boards())
    seed_boards(s, load_seed())
    assert len(s.companies_with_boards()) == before
    s.close()


def test_an_unknown_provider_raises(tmp_path):
    import pytest
    p = tmp_path / "s.yaml"
    p.write_text("boards:\n  - {company: X, provider: greenhoose, token: x}\n")
    with pytest.raises(ValueError, match="greenhoose"):
        load_seed(p)


def test_cli_seed_flag_registers_boards(tmp_path, capsys):
    from jobhunt.ats.poll import main
    db = tmp_path / "j.sqlite"
    assert main(["--db", str(db), "--seed"]) == 0
    assert f"seeded {len(load_seed())} board(s)" in capsys.readouterr().out


def test_a_workday_board_seeds(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("boards:\n  - {company: Draper, provider: workday, "
                 "token: draper.wd5/Draper_Careers}\n")
    assert load_seed(f)[0].provider == "workday"


def test_a_malformed_workday_token_fails_at_seed_time(tmp_path):
    """A typo must fail here, not as a dead board on every poll."""
    import pytest
    f = tmp_path / "s.yaml"
    f.write_text("boards:\n  - {company: Draper, provider: workday, "
                 "token: draper/Draper_Careers}\n")
    with pytest.raises(ValueError, match="Draper"):
        load_seed(f)
