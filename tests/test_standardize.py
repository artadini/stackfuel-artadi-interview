from datetime import date, datetime
from decimal import Decimal

import pytest

from pipeline_functions.helpers import standardize as std


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("  a \t b  ", "a b"),
        ("Köln", "Köln"),
        ("N/A", None),
        ("Na", "Na"),
        ("Nan", "Nan"),  # real first names are not empty markers
    ],
)
def test_clean_text_trims_collapses_and_treats_empty_as_missing(raw, expected):
    assert std.clean_text(raw) == expected


def test_trim_text_keeps_internal_whitespace_of_free_text():
    assert std.trim_text("  zwei  Leerzeichen \n") == "zwei  Leerzeichen"
    assert std.trim_text("   ") is None


def test_ascii_fold_and_match_keys_are_case_umlaut_and_order_insensitive():
    assert std.ascii_fold("Müller") == "mueller"
    assert std.ascii_fold("  Straße ") == "strasse"
    assert std.ascii_fold(None) is None
    assert (
        std.name_match_key("Müller, Ada")
        == std.name_match_key("ADA", "Mueller")
        == "ada mueller"
    )
    assert std.name_match_key("O'Neil-Smith, Jo") == "jo neil o smith"
    assert std.name_match_key("  ") is None


def test_slugify_gives_snake_case_category_values():
    assert (
        std.slugify("Agentur für Arbeit / Jobcenter") == "agentur_fuer_arbeit_jobcenter"
    )
    assert std.slugify(" In-Progress ") == std.slugify("in progress") == "in_progress"
    assert std.slugify("") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  ada  ", "Ada"),
        ("anna-lena", "Anna-Lena"),
        ("o'neil", "O'Neil"),
        ("MÜLLER", "Müller"),
        ("jean  claude", "Jean Claude"),
        ("McDonald", "McDonald"),
        ("van der berg", "Van Der Berg"),
        (None, None),
    ],
)
def test_person_names_capitalize_every_component_and_keep_punctuation(raw, expected):
    assert std.normalize_person_name(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("berlin", "Berlin"),
        ("  FRANKFURT AM MAIN ", "Frankfurt am Main"),
        ("bad homburg", "Bad Homburg"),
        ("garmisch-partenkirchen", "Garmisch-Partenkirchen"),
        ("", None),
    ],
)
def test_city_normalization_is_deterministic_with_german_particles(raw, expected):
    assert std.normalize_city(raw) == expected


def test_email_normalization_and_validation():
    assert std.normalize_email("  Ada.L@Example.COM ") == "ada.l@example.com"
    assert std.normalize_email("  ") is None
    assert std.is_valid_email("a@b.de") is True
    assert std.is_valid_email("not-an-email") is False
    assert std.is_valid_email(None) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-02-28", (date(2026, 2, 28), "parsed")),
        (" 2026-02-28 ", (date(2026, 2, 28), "parsed")),
        ("29.09.2025", (date(2025, 9, 29), "parsed")),
        ("29.09.25", (date(2025, 9, 29), "parsed")),
        (datetime(2026, 4, 13), (date(2026, 4, 13), "parsed")),
        (date(2026, 4, 13), (date(2026, 4, 13), "parsed")),
        ("2026-02-30", (None, "invalid")),
        ("31.02.2026", (None, "invalid")),
        ("04/2026", (None, "invalid")),  # month/year only where the source allows it
        ("garbage", (None, "invalid")),
        ("", (None, "missing")),
        (None, (None, "missing")),
    ],
)
def test_parse_flexible_date_uses_german_formats_and_never_guesses(raw, expected):
    assert std.parse_flexible_date(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("04/2026", (date(2026, 4, 1), "parsed")),
        ("4/26", (date(2026, 4, 1), "parsed")),
        ("12/2024", (date(2024, 12, 1), "parsed")),
        ("29.09.2025", (date(2025, 9, 29), "parsed")),
        ("13/2026", (None, "invalid")),
        ("00/2026", (None, "invalid")),
    ],
)
def test_cohort_dates_accept_month_year(raw, expected):
    assert std.parse_cohort_date(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (9900, (Decimal("9900"), "parsed")),
        ("1.234,56", (Decimal("1234.56"), "parsed")),
        ("3,5", (Decimal("3.5"), "parsed")),
        ("12.5", (Decimal("12.5"), "parsed")),
        ("abc", (None, "invalid")),
        ("1,2,3", (None, "invalid")),
        ("nan", (None, "invalid")),
        (float("inf"), (None, "invalid")),
        (True, (None, "invalid")),
        ("", (None, "missing")),
    ],
)
def test_parse_decimal_follows_german_conventions_and_never_returns_zero_for_garbage(
    raw, expected
):
    assert std.parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("6/10", (Decimal("6"), "10", "scored")),
        ("9", (Decimal("9"), "unknown", "scored")),
        (" 10 / 10 ", (Decimal("10"), "10", "scored")),
        ("3,5", (Decimal("3.5"), "unknown", "scored")),
        ("Alles gut", (None, "text", "text")),
        ("", (None, "unknown", "missing")),
    ],
)
def test_parse_answer_extracts_score_and_context(raw, expected):
    assert std.parse_answer(raw) == expected


@pytest.mark.parametrize(
    "score,category",
    [
        (10, "promoter"),
        (9, "promoter"),
        (8, "passive"),
        (7, "passive"),
        (6, "detractor"),
        (0, "detractor"),
        (None, None),
    ],
)
def test_nps_category_thresholds(score, category):
    assert std.nps_category(score) == category


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("grün", "green"),
        ("GRÜN", "green"),
        ("Gruen", "green"),
        ("g", "green"),
        ("gelb", "yellow"),
        (" Rot ", "red"),
        ("ROT.", "red"),
        ("r", "red"),
    ],
)
def test_ampel_spellings_map_to_green_yellow_red(raw, expected):
    assert std.normalize_ampel(raw) == (expected, "mapped")


def test_ampel_empty_is_unknown_and_never_a_colour():
    assert std.normalize_ampel(None) == ("unknown", "missing")
    assert std.normalize_ampel("  ") == ("unknown", "missing")
    assert std.normalize_ampel("blau") == ("unknown", "unmapped")


@pytest.mark.parametrize(
    "raw,expected",
    [
        (True, (True, "parsed")),
        ("Ja", (True, "parsed")),
        ("1", (True, "parsed")),
        ("nein", (False, "parsed")),
        ("0", (False, "parsed")),
        ("false", (False, "parsed")),
        ("", (None, "missing")),
        (None, (None, "missing")),
        ("vielleicht", (None, "invalid")),
    ],
)
def test_booleans_are_only_converted_when_unambiguous_and_empty_is_never_false(
    raw, expected
):
    assert std.parse_boolean(raw) == expected


def test_standardize_category_returns_snake_case_with_status():
    assert std.standardize_category(" In Progress ") == ("in_progress", "mapped")
    assert std.standardize_category("", ("a",)) == ("unknown", "missing")
    assert std.standardize_category("Zzz", ("a",)) == ("zzz", "unmapped")
    assert std.standardize_category("ABGESCHLOSSEN ", std.ENROLLMENT_STATUSES) == (
        "abgeschlossen",
        "mapped",
    )


def test_federal_state_lookup_ignores_case_and_umlauts():
    assert std.normalize_federal_state("THÜRINGEN") == ("Thüringen", "mapped")
    assert std.normalize_federal_state("thueringen") == ("Thüringen", "mapped")
    assert std.normalize_federal_state("Atlantis") == ("Atlantis", "unmapped")
    assert std.normalize_federal_state(None) == ("unknown", "missing")


def test_split_participant_name_handles_both_workbook_orders():
    assert std.split_participant_name("Ludwig, Piotr") == ("Ludwig", "Piotr")
    assert std.split_participant_name("Jana  Haddad ") == ("Haddad", "Jana")
    assert std.split_participant_name("Hans Peter Meier") == ("Meier", "Hans Peter")
    assert std.split_participant_name("Cher") == ("Cher", None)
    assert std.split_participant_name(None) == (None, None)
