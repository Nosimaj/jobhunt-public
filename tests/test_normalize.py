from jobhunt.normalize import AliasTable, normalize_company, slugify


def test_strips_suffixes_and_punctuation():
    assert normalize_company("Tidewater Corporation") == "tidewater"
    assert normalize_company("Brightwater, Inc") == "brightwater"
    assert normalize_company("Sam Example LLC") == "sam example"


def test_normalizes_dashes_and_slashes():
    assert normalize_company("Contoso — Arcadia") == normalize_company("Contoso Arcadia")


def test_repeated_parent_name_does_not_normalize_to_the_division():
    """Documents a real limitation, resolved by an alias rather than by
    normalisation: the portfolio writes parent and division together."""
    assert normalize_company("Cartwell / Cartwell Health") == "cartwell cartwell health"
    assert normalize_company("Cartwell Health") == "cartwell health"
    assert normalize_company("Cartwell / Cartwell Health") != normalize_company("Cartwell Health")


def test_alias_table_resolves_the_best_buy_pair():
    table = AliasTable([{"canonical": "Cartwell Health",
                         "aliases": ["Cartwell / Cartwell Health", "Cartwell"]}])
    assert table.canonical("Cartwell / Cartwell Health") == "Cartwell Health"
    assert table.canonical("Cartwell Health") == "Cartwell Health"


def test_drops_filler_words_so_bike_repair_matches():
    assert normalize_company("Spokewell Bike Repair") == normalize_company("Spokewell Repair")


def test_alias_table_maps_pdf_name_to_canonical():
    table = AliasTable([{"canonical": "Fernway Solutions",
                         "aliases": ["Brightwater", "Brightwater, Inc"]}])
    assert table.canonical("Brightwater, Inc") == "Fernway Solutions"
    assert table.canonical("Tidewater Corporation") == "Tidewater Corporation"


def test_slugify():
    assert slugify("Tidewater Corporation") == "tidewater-corporation"
    assert slugify("Globex Telecom") == "globex-telecom"
