"""All DuckDB access of the pipeline.

Everything the pipeline stores lives in the database file: the ``raw`` and ``curated`` tables
and the ``meta`` bookkeeping (runs, high watermarks, curation state). No other files are written.
"""

import json
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from .config import (
    DATASETS,
    RAW_LINEAGE_COLUMNS,
    SCHEMA_CURATED,
    SCHEMA_META,
    SCHEMA_RAW,
    table_name,
)
from .schemas import arrow_type, quote

META_DDL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_META}.pipeline_runs (
    run_id VARCHAR NOT NULL,
    snapshot_date DATE NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR NOT NULL,            -- RUNNING | SUCCESS | FAILED
    layers VARCHAR,
    full_refresh BOOLEAN,
    error_type VARCHAR,
    error_message VARCHAR
);
-- One row per snapshot date and dataset: what the extraction did and the high watermark afterwards.
CREATE TABLE IF NOT EXISTS {SCHEMA_META}.extraction_log (
    snapshot_date DATE NOT NULL,
    dataset VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,            -- LOADED (raw table written) | NO_CHANGES (nothing new)
    load_mode VARCHAR NOT NULL,         -- full | incremental
    raw_table VARCHAR,
    extract_from VARCHAR,               -- watermark the API filter was set to (NULL = no filter)
    watermark_field VARCHAR,
    high_watermark VARCHAR,             -- highest watermark_field value seen so far (source text)
    source_fingerprint VARCHAR,         -- snapshot sources: hash of the delivered content
    records_fetched BIGINT NOT NULL,    -- what the source returned
    boundary_rows_skipped BIGINT NOT NULL,  -- already-loaded rows re-delivered at the watermark
    row_count BIGINT NOT NULL,          -- rows written to the raw table
    pages INTEGER,
    http_requests INTEGER,
    http_retries INTEGER,
    extracted_at TIMESTAMPTZ NOT NULL,
    error_type VARCHAR,
    error_message VARCHAR
);
-- One row per snapshot date and dataset: which raw load a curated table was built from.
CREATE TABLE IF NOT EXISTS {SCHEMA_META}.curation_log (
    snapshot_date DATE NOT NULL,
    dataset VARCHAR NOT NULL,
    raw_table VARCHAR NOT NULL,
    raw_run_id VARCHAR NOT NULL,
    curated_table VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    row_count BIGINT NOT NULL,
    valid_count BIGINT NOT NULL,
    review_count BIGINT NOT NULL,
    quarantined_count BIGINT NOT NULL,
    curated_at TIMESTAMPTZ NOT NULL
);
"""


# --------------------------------------------------------------------------------------
# Connection and small helpers
# --------------------------------------------------------------------------------------
def connect(path: Path | str) -> duckdb.DuckDBPyConnection:
    """Open (and create) the database. DuckDB allows one writer at a time."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = duckdb.connect(str(path))
    except duckdb.IOException as error:
        raise RuntimeError(
            f"Could not open {path}; another process may hold the write lock: {error}"
        ) from error
    conn.execute("SET TimeZone = 'UTC'")
    for schema in (SCHEMA_RAW, SCHEMA_CURATED, SCHEMA_META):
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    conn.execute(META_DDL)
    return conn


@contextmanager
def transaction(conn):
    """Everything inside the ``with`` block is committed together or rolled back together."""
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def fetch_dicts(conn, sql: str, params: list | None = None) -> list[dict]:
    """Run a query and return every row as ``{column: value}``."""
    cursor = conn.execute(sql, params or [])
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def column_names(conn, qualified_table: str) -> list[str]:
    return [row[0] for row in conn.execute(f"DESCRIBE {qualified_table}").fetchall()]


def qualified(schema: str, name: str) -> str:
    return f"{schema}.{quote(name)}"


def raw_name(snapshot_date: date, dataset: str) -> str:
    return qualified(SCHEMA_RAW, table_name(snapshot_date, dataset))


def curated_name(snapshot_date: date, dataset: str) -> str:
    return qualified(SCHEMA_CURATED, table_name(snapshot_date, dataset))


def _replace_log_row(conn, log_table: str, entry: dict) -> None:
    """Store one row per (snapshot_date, dataset): delete the old one, insert the new one."""
    table = f"{SCHEMA_META}.{log_table}"
    conn.execute(
        f"DELETE FROM {table} WHERE snapshot_date = ? AND dataset = ?",
        [entry["snapshot_date"], entry["dataset"]],
    )
    columns = column_names(conn, table)
    placeholders = ", ".join("?" * len(columns))
    conn.execute(
        f"INSERT INTO {table} VALUES ({placeholders})",
        [entry.get(column) for column in columns],
    )


# --------------------------------------------------------------------------------------
# Raw layer
# --------------------------------------------------------------------------------------
def to_text(value) -> str | None:
    """The text of a source value exactly as delivered, without interpreting it."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def raw_columns(dataset: str, records: list[dict]) -> list[str]:
    """The known source fields first (a stable schema), then any field the source added."""
    columns = list(DATASETS[dataset].fields)
    for record in records:
        columns += [key for key in record if key not in columns]
    return columns


def write_raw_table(
    conn, dataset: str, snapshot_date: date, records: list[dict], lineage: list[dict]
) -> int:
    """(Re)create ``raw."<date>_<source>_<dataset>"`` holding exactly these records.

    Every source column is text; the ``_``-prefixed lineage columns have real types.
    """
    columns = raw_columns(dataset, records)
    schema = pa.schema(
        [pa.field(name, pa.string()) for name in columns]
        + [
            pa.field(name, arrow_type(sql_type))
            for name, sql_type in RAW_LINEAGE_COLUMNS
        ]
    )
    rows = [
        {**{name: to_text(record.get(name)) for name in columns}, **record_lineage}
        for record, record_lineage in zip(records, lineage, strict=True)
    ]
    conn.register("raw_batch", pa.Table.from_pylist(rows, schema=schema))
    try:
        conn.execute(
            f"CREATE OR REPLACE TABLE {raw_name(snapshot_date, dataset)} AS SELECT * FROM raw_batch"
        )
    finally:
        conn.unregister("raw_batch")
    return len(rows)


def read_raw_table(conn, raw_table: str) -> list[dict]:
    """The rows of a raw table (source columns and lineage) in extraction order."""
    table = qualified(SCHEMA_RAW, raw_table)
    return fetch_dicts(conn, f"SELECT * FROM {table} ORDER BY _source_row_number")


def drop_raw_table(conn, dataset: str, snapshot_date: date) -> None:
    """A same-day rerun that finds nothing new must not leave the first run's table behind."""
    conn.execute(f"DROP TABLE IF EXISTS {raw_name(snapshot_date, dataset)}")


# --------------------------------------------------------------------------------------
# Meta: runs
# --------------------------------------------------------------------------------------
def start_run(
    conn,
    run_id: str,
    snapshot_date: date,
    started_at: datetime,
    layers: str,
    full_refresh: bool,
) -> None:
    conn.execute(
        f"INSERT INTO {SCHEMA_META}.pipeline_runs VALUES (?, ?, ?, NULL, 'RUNNING', ?, ?, NULL, NULL)",
        [run_id, snapshot_date, started_at, layers, full_refresh],
    )


def finish_run(
    conn,
    run_id: str,
    finished_at: datetime,
    status: str,
    *,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    """Complete a pipeline run and store its final status and error details."""
    conn.execute(
        f"""
        UPDATE {SCHEMA_META}.pipeline_runs
        SET finished_at = ?,
            status = ?,
            error_type = ?,
            error_message = ?
        WHERE run_id = ?
        """,
        [finished_at, status, error_type, error_message, run_id],
    )


# --------------------------------------------------------------------------------------
# Meta: extraction state
# --------------------------------------------------------------------------------------
def previous_extractions(conn, dataset: str, before: date) -> list[dict]:
    """Extraction log rows of earlier snapshot dates, oldest first.

    Only earlier dates count, so a rerun for the same date starts from the very same state as
    the first run of that date. That is what makes reruns idempotent.
    """
    return fetch_dicts(
        conn,
        f"SELECT * FROM {SCHEMA_META}.extraction_log WHERE dataset = ? AND snapshot_date < ? ORDER BY snapshot_date",
        [dataset, before],
    )


def log_extraction(conn, entry: dict) -> None:
    """Replace the extraction log row for one snapshot date and dataset."""

    required = {
        "snapshot_date",
        "dataset",
        "source",
        "run_id",
        "status",
        "load_mode",
        "records_fetched",
        "boundary_rows_skipped",
        "row_count",
        "extracted_at",
    }

    missing = sorted(required - entry.keys())
    if missing:
        raise ValueError(f"Extraction log entry is missing required fields: {missing}")

    allowed_statuses = {"LOADED", "NO_CHANGES", "FAILED"}
    if entry["status"] not in allowed_statuses:
        raise ValueError(
            f"Invalid extraction status {entry['status']!r}; "
            f"expected one of {sorted(allowed_statuses)}"
        )

    if entry["status"] == "FAILED" and not entry.get("error_message"):
        raise ValueError("A FAILED extraction log entry must contain error_message")

    _replace_log_row(conn, "extraction_log", entry)


def loaded_on(conn, dataset: str, snapshot_date: date) -> bool:
    """Does a successful raw load of this dataset exist for this snapshot date?"""
    return (
        conn.execute(
            f"SELECT count(*) FROM {SCHEMA_META}.extraction_log WHERE dataset = ? AND snapshot_date = ? AND status = 'LOADED'",
            [dataset, snapshot_date],
        ).fetchone()[0]
        > 0
    )


def stored_row_count(conn, dataset: str, up_to: date) -> int:
    """Rows that raw loads of this dataset hold up to and including this snapshot date.

    Includes the table of ``up_to`` itself, because a same-day rerun would replace it.
    """
    total = conn.execute(
        f"SELECT COALESCE(SUM(row_count), 0) FROM {SCHEMA_META}.extraction_log "
        "WHERE dataset = ? AND status = 'LOADED' AND snapshot_date <= ?",
        [dataset, up_to],
    ).fetchone()[0]
    return int(total)


def stored_rows_at_watermark(
    conn, dataset: str, before: date, field: str, watermark: str
) -> set[tuple]:
    """The already stored rows whose ``field`` equals the watermark, as sets of (column, text) pairs.

    The API filters are inclusive (``>=``), so the newest rows of the previous load come back on
    the next run. Comparing them with these rows keeps the raw layer free of such re-deliveries,
    while a new or changed row (even with the same timestamp) is still different and passes.
    """
    found = set()
    for entry in previous_extractions(conn, dataset, before):
        if entry["status"] != "LOADED":
            continue
        table = qualified(SCHEMA_RAW, entry["raw_table"])
        source_columns = [
            name for name in column_names(conn, table) if not name.startswith("_")
        ]
        if field not in source_columns:
            continue
        select = ", ".join(quote(name) for name in source_columns)
        for row in conn.execute(
            f"SELECT {select} FROM {table} WHERE {quote(field)} = ?", [watermark]
        ).fetchall():
            found.add(tuple(sorted(zip(source_columns, row))))
    return found


# --------------------------------------------------------------------------------------
# Meta: curation state
# --------------------------------------------------------------------------------------
def loaded_raw_tables(conn, dataset: str | None = None) -> list[dict]:
    """The raw tables that exist (extraction log rows with status LOADED), oldest first."""
    sql = f"SELECT * FROM {SCHEMA_META}.extraction_log WHERE status = 'LOADED'"
    params = []
    if dataset:
        sql += " AND dataset = ?"
        params.append(dataset)
    return fetch_dicts(conn, sql + " ORDER BY snapshot_date, dataset", params)


def curation_state(conn) -> dict[tuple[date, str], str]:
    """``{(snapshot_date, dataset): raw_run_id}``: which raw load each curated table was built from."""
    rows = conn.execute(
        f"SELECT snapshot_date, dataset, raw_run_id FROM {SCHEMA_META}.curation_log"
    ).fetchall()
    return {
        (snapshot_date, dataset): raw_run_id
        for snapshot_date, dataset, raw_run_id in rows
    }


def log_curation(conn, entry: dict) -> None:
    _replace_log_row(conn, "curation_log", entry)


def drop_curated_table(conn, dataset: str, snapshot_date: date) -> None:
    """Remove a curated table and its log row (its raw load no longer exists)."""
    conn.execute(f"DROP TABLE IF EXISTS {curated_name(snapshot_date, dataset)}")
    conn.execute(
        f"DELETE FROM {SCHEMA_META}.curation_log WHERE snapshot_date = ? AND dataset = ?",
        [snapshot_date, dataset],
    )
