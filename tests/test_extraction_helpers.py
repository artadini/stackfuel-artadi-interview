"""Unit tests for the helper functions of the extraction layer (no API, in-memory DuckDB)."""

from datetime import date, datetime, timezone

import pytest

from conftest import participant
from pipeline_functions.helpers import database as db
from pipeline_functions.helpers.config import DATASETS
from pipeline_functions.helpers.extraction_types import (
    DatasetResult,
    ExtractionError,
    RunInfo,
)
from pipeline_functions.helpers.extraction_utils import (
    as_comparable_time,
    extraction_run_error,
    high_watermark_of,
    latest_time,
    row_signature,
    sha256_of,
    validate_columns,
)
from pipeline_functions.workflows import extract, runner

UTC = timezone.utc
DAY1, DAY2 = date(2026, 9, 1), date(2026, 9, 2)
PARTICIPANTS = DATASETS["participants"]


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def log(conn, dataset="participants", day=DAY1, status="LOADED", rows=10, **extra):
    entry = {
        "snapshot_date": day,
        "dataset": dataset,
        "source": "crm",
        "run_id": f"run-{day}",
        "status": status,
        "load_mode": "full",
        "records_fetched": rows,
        "boundary_rows_skipped": 0,
        "row_count": rows,
        "extracted_at": datetime(2026, 9, 1, 12, tzinfo=UTC),
    }
    db.log_extraction(conn, entry | extra)


def run_info(day=DAY1) -> RunInfo:
    return RunInfo("run-test", day, datetime(2026, 9, 1, 12, tzinfo=UTC), 500)


# ------------------------------------------------------------------ times and fingerprints
@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-03-01T08:00:00", datetime(2026, 3, 1, 8)),
        ("2026-03-01T08:00:00Z", datetime(2026, 3, 1, 8)),
        ("2026-03-01T08:00:00z", datetime(2026, 3, 1, 8)),
        ("2026-03-01T08:00:00+02:00", datetime(2026, 3, 1, 6)),
        (" 2026-03-01T08:00:00 ", datetime(2026, 3, 1, 8)),
    ],
)
def test_iso_times_become_comparable_naive_utc(text, expected):
    assert as_comparable_time(text) == expected


@pytest.mark.parametrize("value", [None, 5, "", "yesterday", "2026-13-45T00:00:00"])
def test_values_that_are_not_iso_times_are_not_comparable(value):
    assert as_comparable_time(value) is None


def test_latest_time_returns_the_newest_value_as_written():
    values = [
        "2026-03-01T08:00:00",
        "2026-03-01T09:00:00Z",
        "not a time",
        None,
        "2026-03-01T08:30:00+02:00",
    ]
    assert (
        latest_time(values) == "2026-03-01T09:00:00Z"
    )  # 09:00Z is later than 08:00 and 06:30Z
    assert latest_time([]) is None
    assert latest_time(["garbage", None]) is None


def test_high_watermark_is_the_newest_watermark_of_all_previous_loads():
    loads = [
        {"high_watermark": "2026-03-01T08:00:00"},
        {"high_watermark": None},
        {"high_watermark": "2026-03-05T08:00:00"},
    ]
    assert high_watermark_of(loads) == "2026-03-05T08:00:00"
    assert high_watermark_of([]) is None
    assert high_watermark_of([{"high_watermark": None}]) is None


def test_sha256_is_stable_and_content_dependent():
    assert (
        sha256_of(b"abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert sha256_of(b"abc") != sha256_of(b"abd")


# ------------------------------------------------------------------ the text of a source value
@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ("text", "text"),
        (5, "5"),
        (2.5, "2.5"),
        (True, "true"),
        (False, "false"),
        ({"a": 1, "b": [1, 2]}, '{"a":1,"b":[1,2]}'),
        ([{"module_id": "M1"}], '[{"module_id":"M1"}]'),
        (date(2026, 3, 1), "2026-03-01"),
        (datetime(2026, 3, 1, 8), "2026-03-01T08:00:00"),
    ],
)
def test_to_text_keeps_the_value_as_delivered(value, expected):
    assert db.to_text(value) == expected


def test_row_signature_ignores_column_order_and_compares_as_text():
    record = {"id": 1, "active": True, "note": None, "nested": {"x": 1}}
    assert row_signature(["id", "active", "note", "nested"], record) == row_signature(
        ["nested", "note", "active", "id"], record
    )
    assert row_signature(["id", "active"], record) == (("active", "true"), ("id", "1"))
    assert row_signature(["id"], {"id": 1}) == row_signature(
        ["id"], {"id": "1"}
    )  # the stored raw value is text
    assert row_signature(["id", "other"], {"id": 1}) == (
        ("id", "1"),
        ("other", None),
    )  # a missing field is NULL


# ------------------------------------------------------------------ the column contract
def test_columns_that_match_the_contract_are_accepted():
    validate_columns(PARTICIPANTS, set(PARTICIPANTS.fields))


def test_missing_columns_fail_the_dataset():
    columns = set(PARTICIPANTS.fields) - {"email"}
    with pytest.raises(
        ExtractionError, match=r"participants.*missing columns: \['email'\]"
    ):
        validate_columns(PARTICIPANTS, columns)


def test_unexpected_columns_fail_the_dataset():
    with pytest.raises(
        ExtractionError, match=r"unexpected columns: \['loyalty_tier'\]"
    ):
        validate_columns(PARTICIPANTS, set(PARTICIPANTS.fields) | {"loyalty_tier"})


def test_missing_and_unexpected_columns_are_reported_together():
    columns = (set(PARTICIPANTS.fields) - {"email"}) | {"loyalty_tier"}
    with pytest.raises(ExtractionError) as error:
        validate_columns(PARTICIPANTS, columns)
    assert "missing columns" in str(error.value) and "unexpected columns" in str(
        error.value
    )


# ------------------------------------------------------------------ results and run status
def failed(name, error_type="ApiError", message="boom") -> DatasetResult:
    return DatasetResult(
        name, "FAILED", "full", error_type=error_type, error_message=message
    )


def test_no_failed_dataset_means_no_run_error():
    results = [
        DatasetResult("a", "LOADED", "full"),
        DatasetResult("b", "NO_CHANGES", "full"),
    ]
    assert extraction_run_error(results) == (None, None)
    assert extraction_run_error([]) == (None, None)


def test_a_single_failure_keeps_its_error_type():
    results = [
        DatasetResult("a", "LOADED", "full"),
        failed("b", "PaginationError", "total changed"),
    ]
    error_type, message = extraction_run_error(results)
    assert error_type == "PaginationError"
    assert (
        message
        == "1 of 2 dataset extraction(s) failed. b: PaginationError: total changed"
    )


def test_several_failures_are_summarised():
    error_type, message = extraction_run_error(
        [failed("a", message="x"), failed("b", "ExtractionError", "y")]
    )
    assert error_type == "MultipleExtractionErrors"
    assert (
        message
        == "2 of 2 dataset extraction(s) failed. a: ApiError: x | b: ExtractionError: y"
    )


def test_failed_datasets_are_listed_for_the_exit_code():
    summary = {
        "raw_results": [DatasetResult("a", "LOADED", "full"), failed("b"), failed("c")]
    }
    assert runner.failed_datasets(summary) == ["b", "c"]
    assert runner.failed_datasets({"raw_results": []}) == []


def test_dataset_result_summary_is_compact_and_log_safe():
    result = DatasetResult(
        "participants",
        "LOADED",
        "incremental",
        records=[{"id": 1}, {"id": 2}],
        records_fetched=3,
        boundary_rows_skipped=1,
        high_watermark="2026-03-01T08:00:00",
    )
    assert result.summary() == {
        "dataset": "participants",
        "status": "LOADED",
        "load_mode": "incremental",
        "records_fetched": 3,
        "rows_stored": 2,
        "boundary_rows_skipped": 1,
        "high_watermark": "2026-03-01T08:00:00",
        "error_type": None,
        "error_message": None,
    }


# ------------------------------------------------------------------ the extraction log
def test_raw_columns_start_with_the_contract_and_append_new_fields_once():
    columns = db.raw_columns(
        "participants",
        [{"email": "a", "loyalty_tier": "gold"}, {"loyalty_tier": "silver", "x": 1}],
    )
    assert columns[: len(PARTICIPANTS.fields)] == list(PARTICIPANTS.fields)
    assert columns[len(PARTICIPANTS.fields) :] == ["loyalty_tier", "x"]


def test_a_log_entry_needs_its_required_fields(conn):
    with pytest.raises(ValueError, match="missing required fields"):
        db.log_extraction(conn, {"snapshot_date": DAY1, "dataset": "participants"})


def test_a_log_entry_needs_a_known_status(conn):
    with pytest.raises(ValueError, match="Invalid extraction status"):
        log(conn, status="PARTIAL")


def test_a_failed_log_entry_needs_an_error_message(conn):
    with pytest.raises(ValueError, match="error_message"):
        log(conn, status="FAILED")
    log(conn, status="FAILED", error_message="boom", error_type="ApiError")
    assert conn.execute(
        "SELECT status, error_message FROM meta.extraction_log"
    ).fetchall() == [("FAILED", "boom")]


def test_one_log_row_exists_per_day_and_dataset(conn):
    log(conn, rows=3)
    log(conn, rows=7)  # a same-day rerun replaces the row
    assert conn.execute("SELECT row_count FROM meta.extraction_log").fetchall() == [
        (7,)
    ]


def test_previous_extractions_only_return_earlier_days_oldest_first(conn):
    log(conn, day=date(2026, 9, 3))
    log(conn, day=DAY2)
    log(conn, day=DAY1)
    log(conn, dataset="enrollments", day=DAY1)
    assert [
        row["snapshot_date"]
        for row in db.previous_extractions(conn, "participants", date(2026, 9, 3))
    ] == [DAY1, DAY2]
    assert db.previous_extractions(conn, "participants", DAY1) == []


def test_loaded_on_only_counts_a_successful_load_of_that_day(conn):
    log(conn, day=DAY1, status="LOADED")
    log(conn, day=DAY2, status="FAILED", error_message="boom")
    log(conn, dataset="enrollments", day=DAY2, status="NO_CHANGES", rows=0)
    assert db.loaded_on(conn, "participants", DAY1) is True
    assert db.loaded_on(conn, "participants", DAY2) is False
    assert db.loaded_on(conn, "enrollments", DAY2) is False
    assert db.loaded_on(conn, "survey_responses", DAY1) is False


def test_stored_row_count_adds_successful_loads_up_to_and_including_the_day(conn):
    log(conn, day=DAY1, rows=10)
    log(conn, day=DAY2, rows=4)
    log(conn, day=date(2026, 9, 3), rows=100)  # later
    log(conn, dataset="enrollments", day=DAY1, rows=50)  # other dataset
    assert db.stored_row_count(conn, "participants", DAY2) == 14
    assert (
        db.stored_row_count(conn, "participants", DAY1) == 10
    )  # includes the day that a rerun would replace
    assert db.stored_row_count(conn, "survey_responses", DAY2) == 0


def test_stored_row_count_ignores_failed_and_unchanged_loads(conn):
    log(conn, day=DAY1, rows=10)
    log(conn, day=DAY2, status="NO_CHANGES", rows=0)
    log(conn, day=date(2026, 9, 3), status="FAILED", rows=0, error_message="boom")
    assert db.stored_row_count(conn, "participants", date(2026, 9, 3)) == 10


def test_run_status_is_recorded(conn):
    started = datetime(2026, 9, 1, 12, tzinfo=UTC)
    db.start_run(conn, "r1", DAY1, started, "all", False)
    assert conn.execute(
        "SELECT status, finished_at FROM meta.pipeline_runs"
    ).fetchall() == [("RUNNING", None)]
    db.finish_run(
        conn,
        "r1",
        started,
        "PARTIAL_SUCCESS",
        error_type="MultipleExtractionErrors",
        error_message="2 failed",
    )
    assert conn.execute(
        "SELECT status, error_type, error_message FROM meta.pipeline_runs"
    ).fetchall() == [("PARTIAL_SUCCESS", "MultipleExtractionErrors", "2 failed")]


# ------------------------------------------------------------------ the stale-source guard
@pytest.mark.parametrize(
    "fetched, refused",
    [(10, False), (6, False), (5, False), (4, True), (1, True), (0, True)],
)
def test_a_complete_read_below_half_of_the_stored_rows_is_refused(
    conn, fetched, refused
):
    log(conn, rows=10)
    if refused:
        with pytest.raises(
            ExtractionError,
            match=f"returned {fetched} records.*10 rows are already stored.*stale or wrong API",
        ):
            extract.refuse_shrunken_source(conn, PARTICIPANTS, run_info(), fetched)
    else:
        extract.refuse_shrunken_source(conn, PARTICIPANTS, run_info(), fetched)


def test_the_first_load_is_never_refused(conn):
    extract.refuse_shrunken_source(conn, PARTICIPANTS, run_info(), 0)


def test_the_guard_counts_earlier_days_and_the_day_being_replaced(conn):
    log(conn, day=DAY1, rows=6)
    log(
        conn, day=DAY2, rows=6
    )  # total 12, a same-day rerun on DAY2 returning 5 is below half
    with pytest.raises(ExtractionError):
        extract.refuse_shrunken_source(conn, PARTICIPANTS, run_info(DAY2), 5)
    extract.refuse_shrunken_source(conn, PARTICIPANTS, run_info(DAY2), 6)


def test_the_guard_message_names_the_way_out(conn):
    log(conn, rows=10)
    with pytest.raises(ExtractionError, match="--allow-shrinking-source"):
        extract.refuse_shrunken_source(conn, PARTICIPANTS, run_info(), 1)


# ------------------------------------------------------------------ what load_raw does with a failed dataset
def write_participants_load(conn, day=DAY1, count=3):
    records = [participant(i) for i in range(1, count + 1)]
    lineage = [
        {
            "_run_id": "run-1",
            "_ingested_at": datetime(2026, 9, 1, 12, tzinfo=UTC),
            "_snapshot_date": day,
            "_source_object": "/participants",
            "_source_as_of": None,
            "_page_number": 1,
            "_source_row_number": i + 1,
        }
        for i in range(count)
    ]
    db.write_raw_table(conn, "participants", day, records, lineage)
    log(conn, day=day, rows=count, raw_table=f"{day:%Y%m%d}_crm_participants")


def load_failed_participants(conn, day=DAY1):
    return extract.load_raw(
        conn,
        [failed("participants", "ExtractionError", "boom")],
        run_id="run-2",
        snapshot_date=day,
        extracted_at=datetime(2026, 9, 1, 13, tzinfo=UTC),
    )


def test_a_failed_dataset_is_logged_when_the_day_has_no_load_yet(conn):
    assert load_failed_participants(conn) == {"participants": 0}
    assert conn.execute(
        "SELECT status, raw_table, error_message FROM meta.extraction_log"
    ).fetchall() == [("FAILED", None, "boom")]


def test_a_failed_rerun_keeps_the_successful_load_of_the_same_day(conn):
    write_participants_load(conn)

    assert load_failed_participants(conn) == {"participants": 0}

    assert conn.execute(
        "SELECT status, row_count FROM meta.extraction_log"
    ).fetchall() == [("LOADED", 3)]
    assert conn.execute(
        'SELECT count(*) FROM raw."20260901_crm_participants"'
    ).fetchone() == (3,)


def test_a_failed_run_on_a_new_day_leaves_the_earlier_load_alone(conn):
    write_participants_load(conn, day=DAY1)

    load_failed_participants(conn, day=DAY2)

    assert conn.execute(
        "SELECT snapshot_date, status FROM meta.extraction_log ORDER BY 1"
    ).fetchall() == [(DAY1, "LOADED"), (DAY2, "FAILED")]
    assert conn.execute(
        'SELECT count(*) FROM raw."20260901_crm_participants"'
    ).fetchone() == (3,)


def test_a_dataset_without_new_rows_drops_the_same_day_table(conn):
    write_participants_load(conn)
    extract.load_raw(
        conn,
        [DatasetResult("participants", "NO_CHANGES", "incremental")],
        run_id="run-2",
        snapshot_date=DAY1,
        extracted_at=datetime(2026, 9, 1, 13, tzinfo=UTC),
    )
    assert conn.execute(
        "SELECT status, row_count FROM meta.extraction_log"
    ).fetchall() == [("NO_CHANGES", 0)]
    assert not conn.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = '20260901_crm_participants'"
    ).fetchall()


def test_an_unknown_result_status_is_rejected(conn):
    with pytest.raises(ValueError, match="Unsupported extraction status"):
        extract.load_raw(
            conn,
            [DatasetResult("participants", "WEIRD", "full")],
            run_id="run-2",
            snapshot_date=DAY1,
            extracted_at=datetime(2026, 9, 1, 13, tzinfo=UTC),
        )


# ------------------------------------------------------------------ command line
def test_the_shrinking_source_option_is_off_by_default():
    assert runner.parse_args([]).allow_shrinking_source is False


def test_the_shrinking_source_option_can_be_set_by_flag_or_environment(monkeypatch):
    assert (
        runner.parse_args(["--allow-shrinking-source"]).allow_shrinking_source is True
    )
    monkeypatch.setenv("ALLOW_SHRINKING_SOURCE", "yes")
    assert runner.parse_args([]).allow_shrinking_source is True
    monkeypatch.setenv("ALLOW_SHRINKING_SOURCE", "no")
    assert runner.parse_args([]).allow_shrinking_source is False
