import csv
from datetime import date, datetime, timezone

import pytest

from pipeline_functions.helpers.timestamps import parse_timestamp
from pipeline_functions.helpers import training_mapping as tm

UTC = timezone.utc


def test_crm_timestamp_keeps_local_value_and_adds_utc():
    summer = parse_timestamp("2026-07-01T10:30:00", "CRM")
    winter = parse_timestamp("2026-01-01T10:30:00", "CRM")
    assert summer.source_value == datetime(
        2026, 7, 1, 10, 30
    )  # naive Berlin local time
    assert summer.utc_value == datetime(2026, 7, 1, 8, 30, tzinfo=UTC)
    assert winter.utc_value == datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    # Specification example: Berlin 10:15 on 2026-03-04 is 09:15 UTC.
    assert parse_timestamp("2026-03-04T10:15:00", "CRM").utc_value == datetime(
        2026, 3, 4, 9, 15, tzinfo=UTC
    )


@pytest.mark.parametrize("value", ["2026-03-29T02:30:00", "2026-10-25T02:30:00"])
def test_crm_nonexistent_or_ambiguous_local_times_are_invalid_not_guessed(value):
    result = parse_timestamp(value, "CRM")
    assert result.status == "invalid" and result.utc_value is None
    assert result.source_value is not None  # the original value is preserved


def test_lxp_timestamps_are_utc_and_offsets_are_converted():
    expected = datetime(2026, 2, 16, 8, tzinfo=UTC)
    for value in ("2026-02-16T08:00:00Z", "2026-02-16T09:00:00+01:00"):
        result = parse_timestamp(value, "LXP")
        assert (
            result.status == "parsed"
            and result.source_value == result.utc_value == expected
        )


def test_empty_and_garbage_timestamps_have_distinct_statuses():
    assert parse_timestamp(None, "LXP").status == "missing"
    assert parse_timestamp("  ", "CRM").status == "missing"
    bad = parse_timestamp("not-a-date", "LXP")
    assert bad.status == "invalid" and bad.utc_value is None
    assert parse_timestamp("2026-01-01T10:00:00+01:00", "CRM").status == "invalid"


@pytest.mark.parametrize(
    "spelling,training_id",
    [
        ("DS VZ", "T03"),
        ("Data Analyst VZ", "T01"),
        ("Data Analyst (Vollzeit)", "T01"),
        ("Data Analyst – Vollzeit", "T01"),
        ("DA Teilzeit", "T02"),
        ("Data Scientist (Teilzeit)", "T04"),
        ("Data Engineering VZ", "T05"),
        ("Data Engineer (Vollzeit)", "T05"),
        ("Python für Data Analytics", "T06"),
        ("Python (berufsbegl.)", "T06"),
        ("Python f. DA", "T06"),
        ("KI & ML Grundlagen", "T07"),
        ("KI/ML Grundlagen BB", "T07"),
        ("AI Grundlagen", "T07"),
        ("Business Intelligence (Power BI)", "T08"),
        ("BI mit Power BI", "T08"),
        ("PBI VZ", "T08"),
    ],
)
def test_known_training_spellings_map_to_one_id(spelling, training_id):
    match = tm.map_training(spelling)
    assert (match.status, match.training_id) == ("matched", training_id)


def test_track_without_variant_is_ambiguous_and_unknown_is_unmatched():
    ambiguous = tm.map_training("Data Analyst")
    assert ambiguous.status == "ambiguous" and ambiguous.training_id is None
    assert ambiguous.candidate_ids == ("T01", "T02")
    assert tm.map_training("Quantenphysik VZ").status == "unmatched"
    assert tm.map_training("  ").status == "missing"


def test_mapping_table_is_an_explicit_csv_with_valid_ids_only():
    assert (
        tm.MAPPING_FILE.name == "training_mapping.csv"
        and tm.MAPPING_FILE.parent.name == "helpers"
    )
    with tm.MAPPING_FILE.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    ids = {part for row in rows for part in row["training_ids"].split("|")}
    assert ids == {f"T0{number}" for number in range(1, 9)}
    assert all(tm.VALID_TRAINING_ID.match(part) for part in ids)


def test_mapping_loader_rejects_invalid_ids_and_conflicts(tmp_path):
    bad_id = tmp_path / "bad.csv"
    bad_id.write_text("normalized_spelling,training_ids,notes\nfoo,T99,\n")
    with pytest.raises(ValueError, match="Invalid training mapping row"):
        tm.load_training_mapping.__wrapped__(bad_id)
    conflict = tmp_path / "conflict.csv"
    conflict.write_text("normalized_spelling,training_ids,notes\nfoo,T01,\nFoo,T02,\n")
    with pytest.raises(ValueError, match="Conflicting"):
        tm.load_training_mapping.__wrapped__(conflict)
