"""Static configuration of the pipeline: datasets, naming and the curated column contract.

Layout inside the DuckDB database ``stackfuel`` (nothing is written to disk besides the
database file itself):

    stackfuel.raw."<YYYYMMDD>_<source>_<dataset>"      1:1 copy of what the source delivered
    stackfuel.curated."<YYYYMMDD>_<source>_<dataset>"  typed, cleaned, flagged (not deduplicated)
    stackfuel.meta.*                                   run, watermark and curation bookkeeping
"""

import os
from datetime import date
from pathlib import Path

from ..helpers.extraction_types import (
    Dataset,
)

# --------------------------------------------------------------------------------------
# Runtime defaults (override via CLI flags / environment variables, see workflows/runner.py)
# --------------------------------------------------------------------------------------
API_BASE_URL = "http://127.0.0.1:8000"
DATABASE_PATH = Path("db/duckdb/stackfuel.duckdb")
COACH_FILE = Path("data/coach_betreuungsliste.xlsx")
PAGE_SIZE = 500  # the API maximum
# The workbook carries no date; the assignment states it is the 01.09.2026 state.
COACH_SOURCE_AS_OF = date(2026, 9, 1)
# Analysis cut-off ("Stichtag"); only used for non-destructive plausibility flags.
REFERENCE_DATE = date.fromisoformat(os.environ.get("REFERENCE_DATE", "2026-08-31"))

SCHEMA_RAW = "raw"
SCHEMA_CURATED = "curated"
SCHEMA_META = "meta"


# --------------------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------------------
DATASETS: dict[str, Dataset] = {
    d.name: d
    for d in (
        Dataset(
            "trainings",
            "catalog",
            "course_catalog",
            "api_snapshot",
            (
                "training_id",
                "training_name",
                "track",
                "variant",
                "duration_weeks",
                "list_price_eur",
                "is_active",
                "modules",
            ),
            endpoint="/trainings",
            id_field="training_id",
        ),
        Dataset(
            "participants",
            "crm",
            "CRM",
            "api_incremental",
            (
                "participant_id",
                "first_name",
                "last_name",
                "email",
                "birth_date",
                "city",
                "federal_state",
                "funding_type",
                "acquisition_channel",
                "created_at",
                "modified_at",
            ),
            endpoint="/participants",
            id_field="participant_id",
            filter_param="modified_after",
            watermark_field="modified_at",
        ),
        Dataset(
            "enrollments",
            "crm",
            "CRM",
            "api_incremental",
            (
                "enrollment_id",
                "participant_id",
                "training_id",
                "cohort_start_date",
                "planned_end_date",
                "status",
                "status_changed_at",
                "created_at",
                "modified_at",
            ),
            endpoint="/enrollments",
            id_field="enrollment_id",
            filter_param="modified_after",
            watermark_field="modified_at",
        ),
        Dataset(
            "progress_events",
            "lxp",
            "LXP",
            "api_incremental",
            ("event_id", "enrollment_id", "module_id", "event_type", "event_time"),
            endpoint="/progress-events",
            id_field="event_id",
            filter_param="since",
            watermark_field="event_time",
        ),
        Dataset(
            "survey_responses",
            "lxp",
            "LXP",
            "api_incremental",
            (
                "response_id",
                "enrollment_id",
                "survey_type",
                "survey_week",
                "question_key",
                "answer_value",
                "submitted_at",
            ),
            endpoint="/survey-responses",
            id_field="response_id",
            filter_param="since",
            watermark_field="submitted_at",
        ),
        Dataset(
            "coach_betreuungsliste",
            "excel",
            "coach_workbook",
            "workbook",
            (
                "Teilnehmer*in",
                "E-Mail",
                "Training",
                "Kohorte",
                "Coach",
                "Ampel",
                "Letzter Kontakt",
                "Notizen",
            ),
        ),
    )
}
COACH_DATASET = "coach_betreuungsliste"
COACH_SHEET_NAME = "Betreuung"
# Workbook columns without which a row cannot be judged (an empty cell marks the row `incomplete`).
COACH_REQUIRED_COLUMNS = ["Teilnehmer*in", "E-Mail", "Training", "Kohorte", "Coach"]

# Timezone in which the source delivers its (naive or aware) timestamps.
SOURCE_TIMEZONES = {
    "participants": "Europe/Berlin",
    "enrollments": "Europe/Berlin",
    "progress_events": "UTC",
    "survey_responses": "UTC",
}
SOURCE_SYSTEMS = {name: d.source_system for name, d in DATASETS.items()}

# (source field, curated source-value column, curated raw-text column, canonical UTC column)
# CRM source values are Europe/Berlin local time and keep their local value in a TIMESTAMP;
# LXP source values are already UTC and are kept as TIMESTAMPTZ.
TIMESTAMP_SPECS = {
    "participants": [
        ("created_at", "created_at", "created_at_raw", "created_at_utc"),
        ("modified_at", "modified_at", "modified_at_raw", "modified_at_utc"),
    ],
    "enrollments": [
        (
            "status_changed_at",
            "status_changed_at",
            "status_changed_at_raw",
            "status_changed_at_utc",
        ),
        ("created_at", "created_at", "created_at_raw", "created_at_utc"),
        ("modified_at", "modified_at", "modified_at_raw", "modified_at_utc"),
    ],
    "progress_events": [("event_time", "event_at", "event_time_raw", "event_at_utc")],
    "survey_responses": [
        ("submitted_at", "submitted_at", "submitted_at_raw", "submitted_at_utc")
    ],
}


def table_name(snapshot_date: date, dataset: str) -> str:
    """``20260901_crm_participants`` - identical in the raw and the curated schema."""
    return f"{snapshot_date:%Y%m%d}_{DATASETS[dataset].source}_{dataset}"


# --------------------------------------------------------------------------------------
# Raw layer: all source columns are VARCHAR (lossless text of what was delivered); lineage
# columns carry a leading underscore so they can never clash with a source column.
# --------------------------------------------------------------------------------------
RAW_LINEAGE_COLUMNS = [
    ("_run_id", "VARCHAR"),
    ("_ingested_at", "TIMESTAMPTZ"),
    ("_snapshot_date", "DATE"),
    ("_source_object", "VARCHAR"),  # API endpoint or workbook file name
    (
        "_source_as_of",
        "VARCHAR",
    ),  # `as_of` of the catalog response / state date of the workbook
    (
        "_page_number",
        "INTEGER",
    ),  # API page the record arrived on (NULL for the workbook)
    ("_source_row_number", "BIGINT"),  # position in the extraction / sheet row number
]


# --------------------------------------------------------------------------------------
# Curated layer: explicit types, snake_case, `_at` / `_date` / `is_` / `has_` naming
# --------------------------------------------------------------------------------------
CURATED_LINEAGE_COLUMNS = [
    ("run_id", "VARCHAR"),  # run that produced this curated row
    ("curated_at", "TIMESTAMPTZ"),
    ("raw_table", "VARCHAR"),
    ("raw_run_id", "VARCHAR"),
    ("raw_ingested_at", "TIMESTAMPTZ"),
    ("snapshot_date", "DATE"),
    ("source_system", "VARCHAR"),
    ("source_object", "VARCHAR"),
    ("source_as_of_date", "DATE"),
    ("source_row_number", "BIGINT"),
    ("payload_hash", "VARCHAR"),  # SHA-256 of the raw source record
]
# Rows are never dropped or merged here. Duplicates are *counted* within the raw batch and
# flagged; picking the current version of an entity is the job of the next layer.
# `validation_status`: valid | review (usable, needs attention) | quarantined (record-level error;
# the typed value is NULL and the original text is kept in the `*_raw` column).
CURATED_QUALITY_COLUMNS = [
    ("source_id_duplicate_count", "INTEGER"),
    ("payload_duplicate_count", "INTEGER"),
    ("quality_flags", "VARCHAR[]"),
    ("validation_status", "VARCHAR"),
    ("is_quarantined", "BOOLEAN"),
]
NOT_NULL_COLUMNS = {
    "run_id",
    "curated_at",
    "raw_table",
    "raw_run_id",
    "raw_ingested_at",
    "snapshot_date",
    "source_system",
    "source_object",
    "payload_hash",
    "source_id_duplicate_count",
    "payload_duplicate_count",
    "quality_flags",
    "validation_status",
    "is_quarantined",
}

_TS_LOCAL = {"CRM": "TIMESTAMP", "LXP": "TIMESTAMPTZ"}


def _timestamp_columns(name: str) -> list[tuple[str, str]]:
    local_type = _TS_LOCAL[SOURCE_SYSTEMS[name]]
    columns = []
    for _, local_column, raw_column, utc_column in TIMESTAMP_SPECS[name]:
        columns += [
            (local_column, local_type),
            (raw_column, "VARCHAR"),
            (utc_column, "TIMESTAMPTZ"),
        ]
    return columns


CURATED_DOMAIN_COLUMNS = {
    "trainings": [
        ("training_id", "VARCHAR"),
        ("training_id_raw", "VARCHAR"),
        ("training_name", "VARCHAR"),
        ("training_name_raw", "VARCHAR"),
        ("track", "VARCHAR"),
        ("track_raw", "VARCHAR"),
        ("variant", "VARCHAR"),
        ("variant_raw", "VARCHAR"),
        ("duration_weeks", "INTEGER"),
        ("list_price_eur", "DECIMAL(12,2)"),
        ("is_active", "BOOLEAN"),
        ("is_active_status", "VARCHAR"),
        ("has_consistent_modules", "BOOLEAN"),
        ("is_variant_valid", "BOOLEAN"),
        # The complete source object incl. the nested `modules` array (unnested later).
        ("payload_json", "JSON"),
    ],
    "participants": [
        ("participant_id", "VARCHAR"),
        ("first_name", "VARCHAR"),
        ("first_name_raw", "VARCHAR"),
        ("last_name", "VARCHAR"),
        ("last_name_raw", "VARCHAR"),
        ("name_match_key", "VARCHAR"),
        ("email", "VARCHAR"),
        ("email_raw", "VARCHAR"),
        ("is_email_valid", "BOOLEAN"),
        ("email_match_count", "INTEGER"),
        ("birth_date", "DATE"),
        ("birth_date_raw", "VARCHAR"),
        ("is_birth_date_valid", "BOOLEAN"),
        ("city", "VARCHAR"),
        ("city_raw", "VARCHAR"),
        ("federal_state", "VARCHAR"),
        ("federal_state_raw", "VARCHAR"),
        ("funding_type", "VARCHAR"),
        ("funding_type_raw", "VARCHAR"),
        ("acquisition_channel", "VARCHAR"),
        ("acquisition_channel_raw", "VARCHAR"),
        ("canonical_participant_id", "VARCHAR"),
    ]
    + _timestamp_columns("participants")
    + [("source_timezone", "VARCHAR")],
    "enrollments": [
        ("enrollment_id", "VARCHAR"),
        ("participant_id", "VARCHAR"),
        ("training_id", "VARCHAR"),
        ("training_id_raw", "VARCHAR"),
        ("cohort_start_date", "DATE"),
        ("cohort_start_date_raw", "VARCHAR"),
        ("planned_end_date", "DATE"),
        ("planned_end_date_raw", "VARCHAR"),
        ("planned_duration_days", "INTEGER"),
        ("status", "VARCHAR"),
        ("status_raw", "VARCHAR"),
        ("is_status_valid", "BOOLEAN"),
        ("is_open_status", "BOOLEAN"),
        ("has_participant_reference", "BOOLEAN"),
        ("has_training_reference", "BOOLEAN"),
    ]
    + _timestamp_columns("enrollments")
    + [("source_timezone", "VARCHAR")],
    "progress_events": [
        ("event_id", "VARCHAR"),
        ("enrollment_id", "VARCHAR"),
        ("module_id", "VARCHAR"),
        ("module_id_raw", "VARCHAR"),
        ("event_type", "VARCHAR"),
        ("event_type_raw", "VARCHAR"),
        ("is_event_type_valid", "BOOLEAN"),
        ("has_enrollment_reference", "BOOLEAN"),
        ("has_module_reference", "BOOLEAN"),
        ("enrollment_training_id", "VARCHAR"),
        ("module_training_id", "VARCHAR"),
        ("is_module_in_enrollment_training", "BOOLEAN"),
        ("is_event_before_cohort_start", "BOOLEAN"),
    ]
    + _timestamp_columns("progress_events")
    + [("source_timezone", "VARCHAR")],
    "survey_responses": [
        ("response_id", "VARCHAR"),
        ("enrollment_id", "VARCHAR"),
        ("survey_type", "VARCHAR"),
        ("survey_type_raw", "VARCHAR"),
        ("survey_week", "INTEGER"),
        ("question_key", "VARCHAR"),
        ("question_key_raw", "VARCHAR"),
        ("answer_value", "VARCHAR"),
        ("answer_value_raw", "VARCHAR"),
        ("answer_score", "DECIMAL(5,2)"),
        ("answer_context", "VARCHAR"),
        ("is_answer_missing", "BOOLEAN"),
        ("is_answer_valid", "BOOLEAN"),
        ("nps_category", "VARCHAR"),
        ("is_question_valid_for_survey_type", "BOOLEAN"),
        ("is_survey_week_valid", "BOOLEAN"),
        ("has_enrollment_reference", "BOOLEAN"),
    ]
    + _timestamp_columns("survey_responses")
    + [("source_timezone", "VARCHAR")],
    "coach_betreuungsliste": [
        ("coach_assignment_id", "VARCHAR"),
        ("coach_assignment_business_key", "VARCHAR"),
        ("is_business_key_duplicate", "BOOLEAN"),
        ("has_complete_business_key", "BOOLEAN"),
        ("excel_row_number", "BIGINT"),
        ("teilnehmer_raw", "VARCHAR"),
        ("participant_last_name_raw", "VARCHAR"),
        ("participant_first_name_raw", "VARCHAR"),
        ("name_match_key", "VARCHAR"),
        ("email_raw", "VARCHAR"),
        ("email", "VARCHAR"),
        ("email_match_key", "VARCHAR"),
        ("email_assignment_count", "INTEGER"),
        ("training_raw", "VARCHAR"),
        ("training_id", "VARCHAR"),
        ("training_match_status", "VARCHAR"),
        ("kohorte_raw", "VARCHAR"),
        ("cohort_start_date", "DATE"),
        ("cohort_date_parse_status", "VARCHAR"),
        ("coach_raw", "VARCHAR"),
        ("coach", "VARCHAR"),
        ("ampel_raw", "VARCHAR"),
        ("ampel", "VARCHAR"),
        ("ampel_status", "VARCHAR"),
        ("letzter_kontakt_raw", "VARCHAR"),
        ("last_contact_date", "DATE"),
        ("last_contact_date_parse_status", "VARCHAR"),
        ("notizen_raw", "VARCHAR"),
        ("notizen", "VARCHAR"),
        ("completeness_status", "VARCHAR"),
        ("is_incomplete", "BOOLEAN"),
        ("matched_participant_id", "VARCHAR"),
        ("matched_enrollment_id", "VARCHAR"),
        ("match_status", "VARCHAR"),
        ("match_method", "VARCHAR"),
        ("candidate_count", "INTEGER"),
        ("enrollment_candidate_count", "INTEGER"),
        ("enrollment_match_status", "VARCHAR"),
    ],
}

CURATED_COLUMNS = {
    name: columns + CURATED_LINEAGE_COLUMNS + CURATED_QUALITY_COLUMNS
    for name, columns in CURATED_DOMAIN_COLUMNS.items()
}
