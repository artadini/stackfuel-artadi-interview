from datetime import date

import duckdb
import pytest

from conftest import (
    baseline_api,
    event,
    make_client,
    participant,
    training,
    write_workbook,
)
from pipeline_functions.workflows.runner import run_pipeline

DAY1, DAY2, DAY3 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)


def run(args, api, **overrides):
    return run_pipeline(
        args.__class__(**(vars(args) | overrides)), client=make_client(api)
    )


def query(args, sql, params=None):
    conn = duckdb.connect(str(args.duckdb_path), read_only=True)
    try:
        return conn.execute(sql, params or []).fetchall()
    finally:
        conn.close()


def tables(args, schema):
    return sorted(
        name
        for (name,) in query(
            args,
            "SELECT table_name FROM duckdb_tables() WHERE schema_name = ?",
            [schema],
        )
    )


# ------------------------------------------------------------------ naming and 1:1 copy
def test_first_run_creates_one_raw_table_per_dataset_named_date_source_dataset(
    api, make_args
):
    args = make_args(layer="raw")
    summary = run(args, api)

    assert tables(args, "raw") == [
        "20260901_catalog_trainings",
        "20260901_crm_enrollments",
        "20260901_crm_participants",
        "20260901_excel_coach_betreuungsliste",
        "20260901_lxp_progress_events",
        "20260901_lxp_survey_responses",
    ]
    assert summary["raw"] == {
        "trainings": 1,
        "participants": 2,
        "enrollments": 2,
        "progress_events": 2,
        "survey_responses": 2,
        "coach_betreuungsliste": 2,
    }


def test_raw_is_a_one_to_one_copy_as_text_with_lineage(api, make_args):
    args = make_args(layer="raw")
    run(args, api)

    rows = query(
        args,
        "SELECT participant_id, email, birth_date, modified_at, _source_object, _page_number, "
        '_source_row_number, _snapshot_date FROM raw."20260901_crm_participants" ORDER BY 1',
    )
    assert rows == [
        (
            "1",
            "p1@example.com",
            "1990-01-01",
            "2026-03-01T08:00:00",
            "/participants",
            1,
            1,
            DAY1,
        ),
        (
            "2",
            "p2@example.com",
            "1990-01-01",
            "2026-03-02T08:00:00",
            "/participants",
            1,
            2,
            DAY1,
        ),
    ]
    types = dict(
        query(
            args,
            'SELECT column_name, column_type FROM (DESCRIBE raw."20260901_crm_participants")',
        )
    )
    assert {types[c] for c in ("participant_id", "birth_date", "modified_at")} == {
        "VARCHAR"
    }
    assert types["_ingested_at"].startswith("TIMESTAMP WITH TIME ZONE")


def test_nested_modules_and_booleans_survive_unchanged(api, make_args):
    args = make_args(layer="raw")
    run(args, api)
    ((is_active, modules, as_of),) = query(
        args,
        'SELECT is_active, modules, _source_as_of FROM raw."20260901_catalog_trainings"',
    )
    assert is_active == "true" and as_of == "2026-09-01"
    assert modules == (
        '[{"module_id":"T01-M01","module_order":1,"module_name":"Intro","estimated_hours":10},'
        '{"module_id":"T01-M02","module_order":2,"module_name":"SQL","estimated_hours":20}]'
    )


def test_workbook_cells_are_copied_with_sheet_row_numbers(api, make_args):
    args = make_args(layer="raw")
    run(args, api)
    rows = query(
        args,
        'SELECT "Teilnehmer*in", "Kohorte", "Letzter Kontakt", "Ampel", _source_row_number, _source_object '
        'FROM raw."20260901_excel_coach_betreuungsliste" ORDER BY _source_row_number',
    )
    assert rows == [
        (
            "Last1, First1",
            "2026-02-02T00:00:00",
            "2026-08-25T00:00:00",
            "rot",
            2,
            "coach.xlsx",
        ),
        ("First2 Last2", "02/2026", "14.06.26", "grün", 3, "coach.xlsx"),
    ]


def test_a_dataset_with_unexpected_columns_fails_alone_and_the_run_is_partial(
    api, make_args
):
    api.data["/participants"][0]["loyalty_tier"] = "gold"
    args = make_args(layer="raw")
    run(args, api)

    assert "20260901_crm_participants" not in tables(args, "raw")
    assert len(tables(args, "raw")) == 5  # every other dataset is loaded
    assert query(
        args, "SELECT status FROM meta.extraction_log WHERE dataset = 'participants'"
    ) == [("FAILED",)]
    assert query(args, "SELECT status FROM meta.pipeline_runs") == [
        ("PARTIAL_SUCCESS",)
    ]


@pytest.mark.parametrize("count", [1, 40, 500, 501, 1200])
def test_every_record_is_extracted_exactly_once(api, make_args, count):
    api.data["/progress-events"] = [
        event(
            i, event_time=f"2026-03-01T{i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z"
        )
        for i in range(count)
    ]
    args = make_args(layer="raw")
    run(args, api)
    ((rows, distinct),) = query(
        args,
        'SELECT count(*), count(DISTINCT event_id) FROM raw."20260901_lxp_progress_events"',
    )
    assert rows == distinct == count


def test_no_files_are_written_besides_the_database(api, make_args, tmp_path):
    args = make_args()
    run(args, api)
    created = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert created == {"coach.xlsx", "db/stackfuel.duckdb"}


# ------------------------------------------------------------------ idempotency
def test_same_day_rerun_replaces_instead_of_duplicating(api, make_args):
    args = make_args(layer="raw")
    run(args, api)
    before = query(
        args, 'SELECT * FROM raw."20260901_lxp_progress_events" ORDER BY event_id'
    )
    run(args, api)
    after = query(
        args, 'SELECT * FROM raw."20260901_lxp_progress_events" ORDER BY event_id'
    )

    assert len(after) == 2
    assert [row[:5] for row in before] == [row[:5] for row in after]
    assert query(
        args,
        "SELECT count(*) FROM meta.extraction_log WHERE dataset = 'progress_events'",
    ) == [(1,)]
    assert len(tables(args, "raw")) == 6  # still one table per dataset


# ------------------------------------------------------------------ incremental (high watermark)
def test_next_day_without_changes_stores_nothing_but_remembers_the_watermark(
    api, make_args
):
    args = make_args(layer="raw")
    run(args, api)
    summary = run(args, api, snapshot_date=DAY2)

    assert summary["raw"] == {name: 0 for name in summary["raw"]}
    assert len(tables(args, "raw")) == 6  # no table for day 2
    rows = query(
        args,
        "SELECT dataset, status, load_mode, extract_from, high_watermark, records_fetched, "
        "boundary_rows_skipped FROM meta.extraction_log WHERE snapshot_date = ? ORDER BY dataset",
        [DAY2],
    )
    by_dataset = {row[0]: row[1:] for row in rows}
    # inclusive filter: the newest already-loaded row is delivered again and recognised
    assert by_dataset["participants"] == (
        "NO_CHANGES",
        "incremental",
        "2026-03-02T08:00:00",
        "2026-03-02T08:00:00",
        1,
        1,
    )
    assert by_dataset["progress_events"] == (
        "NO_CHANGES",
        "incremental",
        "2026-03-02T10:00:00Z",
        "2026-03-02T10:00:00Z",
        1,
        1,
    )
    assert by_dataset["trainings"][0] == "NO_CHANGES"
    assert by_dataset["coach_betreuungsliste"][0] == "NO_CHANGES"


def test_next_day_loads_only_new_and_changed_records_using_the_high_watermark(
    api, make_args
):
    args = make_args(layer="raw")
    run(args, api)
    api.requests.clear()
    api.data["/participants"] += [participant(3, "2026-03-05T08:00:00")]  # new
    api.data["/participants"][0] = participant(
        1, "2026-03-06T08:00:00", city="Hamburg"
    )  # changed
    api.data["/progress-events"].append(event(3, 1, "2026-03-07T10:00:00Z"))
    summary = run(args, api, snapshot_date=DAY2)

    assert (
        summary["raw"]["participants"] == 2 and summary["raw"]["progress_events"] == 1
    )
    assert summary["raw"]["enrollments"] == 0
    sent = {path: query_ for path, query_ in api.requests}
    assert sent["/participants"]["modified_after"] == "2026-03-02T08:00:00"
    assert sent["/progress-events"]["since"] == "2026-03-02T10:00:00Z"
    assert query(
        args,
        'SELECT participant_id, city FROM raw."20260902_crm_participants" ORDER BY 1',
    ) == [("1", "Hamburg"), ("3", "Berlin")]
    assert query(args, 'SELECT event_id FROM raw."20260902_lxp_progress_events"') == [
        ("ev-0000003",)
    ]

    # the watermark moves forward for the run after that
    api.requests.clear()
    run(args, api, snapshot_date=DAY3)
    assert (
        dict(api.requests)["/participants"]["modified_after"] == "2026-03-06T08:00:00"
    )
    assert dict(api.requests)["/progress-events"]["since"] == "2026-03-07T10:00:00Z"


def test_a_new_record_with_the_same_timestamp_as_the_watermark_is_not_lost(
    api, make_args
):
    args = make_args(layer="raw")
    run(args, api)
    api.data["/progress-events"].append(
        event(3, 2, "2026-03-02T10:00:00Z")
    )  # same second as the watermark
    summary = run(args, api, snapshot_date=DAY2)
    assert summary["raw"]["progress_events"] == 1
    assert query(args, 'SELECT event_id FROM raw."20260902_lxp_progress_events"') == [
        ("ev-0000003",)
    ]


def test_rerun_of_the_second_day_is_idempotent(api, make_args):
    args = make_args(layer="raw")
    run(args, api)
    api.data["/participants"].append(participant(3, "2026-03-05T08:00:00"))
    run(args, api, snapshot_date=DAY2)
    run(args, api, snapshot_date=DAY2)
    assert query(args, 'SELECT count(*) FROM raw."20260902_crm_participants"') == [(1,)]


def test_full_refresh_ignores_the_watermark(api, make_args):
    args = make_args(layer="raw")
    run(args, api)
    summary = run(args, api, snapshot_date=DAY2, full_refresh=True)
    assert summary["raw"]["participants"] == 2 and summary["raw"]["trainings"] == 1


def test_changed_catalog_and_workbook_are_loaded_again(api, make_args, coach_file):
    args = make_args(layer="raw")
    run(args, api)
    api.trainings = [training("T01", list_price_eur=10900)]
    write_workbook(
        coach_file,
        [
            [
                "Only Row",
                "x@example.com",
                "DA VZ",
                "2026-02-02",
                "Coach A",
                "gelb",
                None,
                None,
            ]
        ],
    )
    summary = run(args, api, snapshot_date=DAY2)
    assert (
        summary["raw"]["trainings"] == 1
        and summary["raw"]["coach_betreuungsliste"] == 1
    )
    assert query(
        args, 'SELECT list_price_eur FROM raw."20260902_catalog_trainings"'
    ) == [("10900",)]


# ------------------------------------------------------------------ failure handling
def test_a_failing_source_is_recorded_and_does_not_advance_its_watermark(
    api, make_args
):
    args = make_args(layer="raw")
    run(args, api)
    api.data["/participants"].append(participant(3, "2026-03-05T08:00:00"))
    original = api.__call__

    def broken(request, timeout=None):
        if "survey-responses" in request.full_url and "since" in request.full_url:
            raise ConnectionRefusedError("down")
        return original(request, timeout)

    client = make_client(api)
    client._open = broken
    client.max_attempts = 2
    run_pipeline(
        args.__class__(**(vars(args) | {"snapshot_date": DAY2})), client=client
    )

    assert "20260902_lxp_survey_responses" not in tables(
        args, "raw"
    )  # nothing for the failed dataset
    assert "20260902_crm_participants" in tables(args, "raw")  # the others are loaded
    assert query(
        args,
        "SELECT status FROM meta.extraction_log WHERE snapshot_date = ? AND dataset = 'survey_responses'",
        [DAY2],
    ) == [("FAILED",)]
    assert query(
        args, "SELECT status FROM meta.pipeline_runs ORDER BY started_at DESC LIMIT 1"
    ) == [("PARTIAL_SUCCESS",)]

    # once the source is back, the failed dataset loads from its old watermark
    run(args, api, snapshot_date=DAY2)
    assert query(
        args,
        "SELECT status FROM meta.extraction_log WHERE snapshot_date = ? AND dataset = 'survey_responses'",
        [DAY2],
    ) == [("NO_CHANGES",)]


def test_missing_workbook_fails_the_coach_dataset(api, make_args, tmp_path):
    args = make_args(layer="raw", coach_file=tmp_path / "missing.xlsx")
    run(args, api)
    assert "20260901_excel_coach_betreuungsliste" not in tables(args, "raw")
    assert len(tables(args, "raw")) == 5
    assert (
        query(args, "SELECT error_message FROM meta.pipeline_runs")[0][0].find(
            "not found"
        )
        > -1
    )


# ------------------------------------------------------------------ a stale or wrong API
def test_a_complete_read_that_shrinks_the_source_fails_that_dataset_and_keeps_the_good_load(
    api, make_args
):
    api.data["/participants"] = [participant(i) for i in range(1, 11)]
    args = make_args(layer="all")
    run(args, api)

    stale = baseline_api()  # an old server that still answers with 2 participants
    summary = run(args, stale)

    assert query(args, 'SELECT count(*) FROM raw."20260901_crm_participants"') == [
        (10,)
    ]
    assert query(args, 'SELECT count(*) FROM curated."20260901_crm_participants"') == [
        (10,)
    ]  # not dropped
    assert query(
        args,
        "SELECT status, row_count FROM meta.extraction_log WHERE dataset = 'participants'",
    ) == [("LOADED", 10)]
    assert (
        query(
            args,
            "SELECT status, error_message FROM meta.pipeline_runs ORDER BY started_at DESC LIMIT 1",
        )[0][0]
        == "PARTIAL_SUCCESS"
    )
    failed = [result for result in summary["raw_results"] if result.status == "FAILED"]
    assert [result.dataset for result in failed] == [
        "participants"
    ] and "stale or wrong API" in failed[0].error_message


def test_a_shrinking_source_is_accepted_when_explicitly_allowed(api, make_args):
    api.data["/participants"] = [participant(i) for i in range(1, 11)]
    args = make_args(layer="raw")
    run(args, api)

    summary = run(args, baseline_api(), allow_shrinking_source=True)

    assert summary["raw"]["participants"] == 2
    assert query(args, 'SELECT count(*) FROM raw."20260901_crm_participants"') == [(2,)]
