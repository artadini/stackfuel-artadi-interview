from datetime import date, datetime, timezone
from decimal import Decimal

import duckdb
import pytest

from conftest import enrollment, event, make_client, participant, survey
from pipeline_functions.helpers.config import CURATED_COLUMNS
from pipeline_functions.helpers.schemas import (
    SchemaViolation,
    arrow_schema,
    create_table_sql,
    enforce_schema,
)
from pipeline_functions.workflows.runner import run_pipeline

DAY2 = date(2026, 9, 2)
UTC = timezone.utc


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


def curated(args, name, columns="*", where="1=1", order="1"):
    return query(
        args, f'SELECT {columns} FROM curated."{name}" WHERE {where} ORDER BY {order}'
    )


# ------------------------------------------------------------------ naming, typing
def test_curated_tables_mirror_the_raw_tables(api, make_args):
    args = make_args()
    summary = run(args, api)

    raw = dict(
        query(
            args,
            "SELECT table_name, estimated_size FROM duckdb_tables() WHERE schema_name = 'raw'",
        )
    )
    cur = dict(
        query(
            args,
            "SELECT table_name, estimated_size FROM duckdb_tables() WHERE schema_name = 'curated'",
        )
    )
    assert sorted(raw) == sorted(cur) and len(cur) == 6
    assert sum(summary["curated"].values()) == sum(summary["raw"].values())
    for name in cur:
        assert query(args, f'SELECT count(*) FROM curated."{name}"') == query(
            args, f'SELECT count(*) FROM raw."{name}"'
        )


def test_curated_columns_have_explicit_types(api, make_args):
    run(args := make_args(), api)
    types = dict(
        query(
            args,
            'SELECT column_name, column_type FROM (DESCRIBE curated."20260901_crm_enrollments")',
        )
    )
    assert types["enrollment_id"] == "VARCHAR"
    assert types["cohort_start_date"] == "DATE"
    assert types["created_at"] == "TIMESTAMP"  # CRM local time
    assert types["modified_at_utc"] == "TIMESTAMP WITH TIME ZONE"
    assert types["is_open_status"] == "BOOLEAN"
    assert types["quality_flags"] == "VARCHAR[]"
    survey_types = dict(
        query(
            args,
            'SELECT column_name, column_type FROM (DESCRIBE curated."20260901_lxp_survey_responses")',
        )
    )
    assert (
        survey_types["survey_week"] == "INTEGER"
        and survey_types["answer_score"] == "DECIMAL(5,2)"
    )
    assert (
        dict(
            query(
                args,
                'SELECT column_name, column_type FROM (DESCRIBE curated."20260901_catalog_trainings")',
            )
        )["payload_json"]
        == "JSON"
    )


def test_not_null_lineage_columns_are_enforced_by_the_table(api, make_args):
    run(args := make_args(), api)
    nullable = dict(
        query(
            args,
            "SELECT column_name, is_nullable FROM duckdb_columns() "
            "WHERE schema_name = 'curated' AND table_name = '20260901_crm_participants'",
        )
    )
    assert nullable["payload_hash"] is False and nullable["validation_status"] is False
    assert nullable["participant_id"] is True


# ------------------------------------------------------------------ transformation
def test_timestamps_are_normalised_to_utc(api, make_args):
    api.data["/participants"] = [
        participant(1, "2026-01-15T10:00:00"),  # winter: CET = UTC+1
        participant(2, "2026-07-01T10:00:00"),  # summer: CEST = UTC+2
    ]
    api.data["/progress-events"] = [event(1, 1, "2026-07-01T10:00:00Z")]
    run(args := make_args(), api)

    rows = curated(
        args,
        "20260901_crm_participants",
        "participant_id, modified_at, modified_at_raw, modified_at_utc, source_timezone",
    )
    assert rows[0][1:] == (
        datetime(2026, 1, 15, 10),
        "2026-01-15T10:00:00",
        datetime(2026, 1, 15, 9, tzinfo=UTC),
        "Europe/Berlin",
    )
    assert rows[1][3] == datetime(2026, 7, 1, 8, tzinfo=UTC)
    ((event_at, tz),) = curated(
        args, "20260901_lxp_progress_events", "event_at_utc, source_timezone"
    )
    assert event_at == datetime(2026, 7, 1, 10, tzinfo=UTC) and tz == "UTC"


def test_values_are_standardised_and_the_original_is_kept(api, make_args):
    api.data["/participants"] = [
        participant(
            1,
            first_name="  anna-lena ",
            last_name="MÜLLER",
            email=" Anna@Example.COM ",
            city="frankfurt am main",
            funding_type="Bildungsgutschein",
        )
    ]
    run(args := make_args(), api)
    (row,) = curated(
        args,
        "20260901_crm_participants",
        "first_name, first_name_raw, last_name, email, email_raw, city, funding_type, funding_type_raw",
    )
    assert row == (
        "Anna-Lena",
        "  anna-lena ",
        "Müller",
        "anna@example.com",
        " Anna@Example.COM ",
        "Frankfurt am Main",
        "bildungsgutschein",
        "Bildungsgutschein",
    )


def test_unreadable_values_become_null_keep_the_original_and_flag_the_row(
    api, make_args
):
    api.data["/participants"] = [
        participant(1, birth_date="not-a-date"),
        participant(2),
    ]
    api.data["/survey-responses"] = [
        survey(1, survey_week="abc"),
        survey(2, answer_value="4"),
    ]
    run(args := make_args(), api)

    bad, good = curated(
        args,
        "20260901_crm_participants",
        "birth_date, birth_date_raw, validation_status, is_quarantined, quality_flags",
        order="participant_id",
    )
    assert bad == (None, "not-a-date", "quarantined", True, ["invalid_birth_date"])
    assert good[2:4] == ("valid", False)
    ((week, status, flags),) = curated(
        args,
        "20260901_lxp_survey_responses",
        "survey_week, validation_status, quality_flags",
        where="response_id = '1'",
    )
    assert week is None and status == "quarantined" and "invalid_survey_week" in flags
    assert curated(
        args, "20260901_lxp_survey_responses", "survey_week", where="response_id = '2'"
    ) == [(1,)]


def test_broken_references_are_flagged_not_dropped(api, make_args):
    api.data["/enrollments"] = [enrollment(1, 1), enrollment(2, 999)]
    api.data["/progress-events"] = [
        event(1, 1),
        event(2, 555),
        event(3, 1, module_id="T99-M01"),
    ]
    run(args := make_args(), api)

    assert curated(
        args,
        "20260901_crm_enrollments",
        "enrollment_id, has_participant_reference, has_training_reference",
    ) == [("1", True, True), ("2", False, True)]
    rows = curated(
        args,
        "20260901_lxp_progress_events",
        "event_id, has_enrollment_reference, has_module_reference, quality_flags",
    )
    assert rows[0][1:3] == (True, True)
    assert rows[1][1] is False and "missing_enrollment_reference" in rows[1][3]
    assert rows[2][2] is False and "missing_module_reference" in rows[2][3]


def test_duplicates_are_counted_and_flagged_but_never_removed(api, make_args):
    api.data["/progress-events"] = [
        event(1, 1, "2026-03-01T10:00:00Z"),
        event(1, 1, "2026-03-01T10:00:00Z"),
        event(2, 1, "2026-03-01T11:00:00Z"),
    ]
    api.data["/participants"] = [
        participant(1, "2026-03-01T08:00:00"),
        participant(1, "2026-03-02T08:00:00", city="Köln"),
    ]
    run(args := make_args(), api)

    events = curated(
        args,
        "20260901_lxp_progress_events",
        "event_id, source_id_duplicate_count, payload_duplicate_count, quality_flags",
    )
    assert [row[:3] for row in events] == [
        ("ev-0000001", 2, 2),
        ("ev-0000001", 2, 2),
        ("ev-0000002", 1, 1),
    ]
    assert (
        "duplicate_source_id" in events[0][3]
        and "exact_duplicate_source_payload" in events[1][3]
    )
    participants = curated(
        args,
        "20260901_crm_participants",
        "participant_id, city, source_id_duplicate_count, payload_duplicate_count",
    )
    assert participants == [
        ("1", "Berlin", 2, 1),
        ("1", "Köln", 2, 1),
    ]  # both versions kept; ids repeated, payloads differ


def test_survey_answers_are_parsed_without_inventing_values(api, make_args):
    api.data["/survey-responses"] = [
        survey(
            1,
            survey_type="abschlussfeedback",
            survey_week=None,
            question_key="nps",
            answer_value="9",
        ),
        survey(
            2,
            survey_type="abschlussfeedback",
            survey_week=None,
            question_key="nps",
            answer_value="6/10",
        ),
        survey(
            3,
            survey_type="abschlussfeedback",
            survey_week=None,
            question_key="nps",
            answer_value="eleven",
        ),
        survey(4, question_key="freitext", answer_value="gut"),
    ]
    run(args := make_args(), api)
    rows = curated(
        args,
        "20260901_lxp_survey_responses",
        "response_id, answer_score, nps_category, validation_status, survey_week",
    )
    assert rows[0] == ("1", Decimal("9.00"), "promoter", "valid", None)
    assert rows[1][1:3] == (Decimal("6.00"), "detractor")
    assert rows[2][1:4] == (None, None, "quarantined")
    assert rows[3][1] is None and rows[3][3] == "valid"


# ------------------------------------------------------------------ coach workbook
def test_coach_rows_are_matched_to_enrollments_with_a_documented_ladder(api, make_args):
    run(args := make_args(), api)
    rows = curated(
        args,
        "20260901_excel_coach_betreuungsliste",
        "excel_row_number, training_id, cohort_start_date, ampel, last_contact_date, match_status, match_method, "
        "matched_participant_id, matched_enrollment_id",
        order="excel_row_number",
    )
    assert rows[0] == (
        2,
        "T01",
        date(2026, 2, 2),
        "red",
        date(2026, 8, 25),
        "matched",
        "unique_email",
        "1",
        "1",
    )
    assert rows[1] == (
        3,
        "T01",
        date(2026, 2, 1),
        "green",
        date(2026, 6, 14),
        "matched",
        "name_training",
        "2",
        "2",
    )
    ids = {
        row[0]
        for row in curated(
            args, "20260901_excel_coach_betreuungsliste", "coach_assignment_id"
        )
    }
    assert len(ids) == 2 and all(
        len(i) == 64 for i in ids
    )  # deterministic SHA-256 per row


# ------------------------------------------------------------------ incremental curation
def test_unchanged_raw_tables_are_not_curated_again(api, make_args):
    args = make_args()
    run(args, api)
    stamp = query(
        args, 'SELECT max(curated_at) FROM curated."20260901_crm_participants"'
    )
    summary = run(args, api, snapshot_date=DAY2)  # nothing new at the source
    assert summary["curated"] == {}
    assert (
        query(args, 'SELECT max(curated_at) FROM curated."20260901_crm_participants"')
        == stamp
    )


def test_only_the_new_raw_batch_is_curated_and_checked_against_the_full_history(
    api, make_args
):
    args = make_args()
    run(args, api)
    api.data["/enrollments"].append(
        enrollment(3, 2, "2026-03-09T08:00:00")
    )  # participant 2 was loaded on day 1
    api.data["/participants"].append(participant(3, "2026-03-09T08:00:00"))
    summary = run(args, api, snapshot_date=DAY2)

    assert set(summary["curated"]) == {
        "curated.20260902_crm_enrollments",
        "curated.20260902_crm_participants",
    }
    assert curated(
        args,
        "20260902_crm_enrollments",
        "enrollment_id, has_participant_reference, has_training_reference",
    ) == [
        ("3", True, True)
    ]  # the participant is from day 1 and the training from the catalog table
    assert query(args, 'SELECT count(*) FROM curated."20260901_crm_enrollments"') == [
        (2,)
    ]


def test_same_day_rerun_rebuilds_the_curated_table_without_duplicates(api, make_args):
    args = make_args()
    run(args, api)
    first = curated(args, "20260901_crm_participants", "participant_id, payload_hash")
    summary = run(args, api)
    assert summary["curated"]["curated.20260901_crm_participants"] == 2
    assert (
        curated(args, "20260901_crm_participants", "participant_id, payload_hash")
        == first
    )
    assert query(
        args, "SELECT count(*) FROM meta.curation_log WHERE dataset = 'participants'"
    ) == [(1,)]


def test_curated_table_of_a_vanished_raw_load_is_dropped(api, make_args):
    args = make_args()
    run(args, api)
    api.data["/participants"].append(participant(3, "2026-03-09T08:00:00"))
    run(args, api, snapshot_date=DAY2)
    assert curated(args, "20260902_crm_participants", "participant_id") == [("3",)]
    api.data[
        "/participants"
    ].pop()  # source rolled back; same-day rerun finds nothing new
    run(args, api, snapshot_date=DAY2)
    tables = {
        name
        for (name,) in query(
            args,
            "SELECT table_name FROM duckdb_tables() WHERE schema_name IN ('raw', 'curated')",
        )
    }
    assert "20260902_crm_participants" not in tables


def test_rebuild_curates_everything_again(api, make_args):
    args = make_args()
    run(args, api)
    summary = run(args, api, snapshot_date=DAY2, layer="curated", rebuild_curated=True)
    assert len(summary["curated"]) == 6


def test_curation_runs_without_extraction_on_existing_raw_data(api, make_args):
    args = make_args()
    run(args, api, layer="raw")
    assert query(
        args, "SELECT count(*) FROM duckdb_tables() WHERE schema_name = 'curated'"
    ) == [(0,)]
    summary = run(args, api, layer="curated")
    assert len(summary["curated"]) == 6


# ------------------------------------------------------------------ schema enforcement (Parquet types)
SPEC = [
    ("run_id", "VARCHAR"),
    ("n", "INTEGER"),
    ("amount", "DECIMAL(5,2)"),
    ("day", "DATE"),
    ("at_utc", "TIMESTAMPTZ"),
    ("flags", "VARCHAR[]"),
]


def good_row(**overrides):
    return {
        "run_id": "r",
        "n": 1,
        "amount": Decimal("1.50"),
        "day": date(2026, 1, 1),
        "at_utc": datetime(2026, 1, 1, tzinfo=UTC),
        "flags": ["a"],
    } | overrides


def test_enforce_schema_accepts_matching_rows_and_keeps_the_schema():
    table = enforce_schema([good_row(), good_row(n=None, flags=[])], SPEC)
    assert table.schema.equals(arrow_schema(SPEC), check_metadata=False)
    assert table.num_rows == 2
    assert (
        not table.schema.field("run_id").nullable and table.schema.field("n").nullable
    )


@pytest.mark.parametrize(
    "row",
    [
        good_row(n="1"),  # wrong type
        good_row(n=2**40),  # does not fit INTEGER
        good_row(amount=Decimal("1000.00")),  # does not fit DECIMAL(5,2)
        good_row(day="2026-01-01"),  # text instead of date
        good_row(run_id=None),  # NULL in a NOT NULL column
        good_row(flags="a"),  # not a list
        {**good_row(), "surprise": 1},  # unexpected column
        {
            key: value for key, value in good_row().items() if key != "n"
        },  # missing column
    ],
)
def test_enforce_schema_rejects_rows_that_do_not_fit(row):
    with pytest.raises(SchemaViolation):
        enforce_schema([row], SPEC)


def test_every_curated_column_has_a_type_mapping_and_ddl():
    for dataset, columns in CURATED_COLUMNS.items():
        assert len(arrow_schema(columns)) == len(columns), dataset
        assert len({name for name, _ in columns}) == len(
            columns
        ), f"duplicate column in {dataset}"
        assert "NOT NULL" in create_table_sql("t", columns)
