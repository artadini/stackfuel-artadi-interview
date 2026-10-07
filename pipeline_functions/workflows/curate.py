"""Curate: ``stackfuel.raw`` -> ``stackfuel.curated`` (clean and type, do not deduplicate).

* Every raw table without a curated table yet (or whose raw load was replaced since) is curated
  into the curated table of the same name. ``rebuild=True`` curates all of them again, which is
  needed when reference data arrived late.
* Rows are never dropped or merged. A value that cannot be read becomes NULL, the original text
  stays in the ``*_raw`` column, and the row is flagged. Duplicates are only counted and flagged;
  choosing the current version of a record is the job of the next layer.
* References (does the participant, enrollment or module exist?) are checked against the whole raw
  history of those datasets, not only against the batch being curated.
* Every batch must fit the curated schema (Arrow / Parquet types) before it is inserted.
"""

import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import datetime

from ..helpers.coach_curation import curate_coach_rows
from ..helpers.config import (
    CURATED_COLUMNS,
    COACH_DATASET,
    DATASETS,
    SCHEMA_CURATED,
    SOURCE_SYSTEMS,
    table_name,
)
from ..helpers.curation import ENRICHERS, Context, build_context, id_str
from ..helpers.database import (
    curated_name,
    curation_state,
    drop_curated_table,
    loaded_raw_tables,
    log_curation,
    read_raw_table,
)
from ..helpers.logging_utils import get_logger
from ..helpers.quarantine import error, validation_status
from ..helpers.schemas import create_table_sql, enforce_schema
from ..helpers.standardize import parse_iso_date

logger = get_logger(__name__)
REFERENCE_DATASETS = ("trainings", "participants", "enrollments")


# --------------------------------------------------------------------------------------
# Reading raw rows
# --------------------------------------------------------------------------------------
def split_raw_row(raw_row: dict) -> tuple[dict, dict]:
    """A raw row as ``(source columns, lineage columns)``; lineage columns start with ``_``."""
    source = {
        name: value for name, value in raw_row.items() if not name.startswith("_")
    }
    lineage = {name: value for name, value in raw_row.items() if name.startswith("_")}
    return source, lineage


def decode_record(dataset: str, source: dict) -> dict:
    """The record as the cleaning rules expect it: text, except the nested module list of a training."""
    record = dict(source)
    if dataset == "trainings":
        try:
            modules = json.loads(record["modules"]) if record.get("modules") else None
        except json.JSONDecodeError:
            modules = None
        record["modules"] = modules if isinstance(modules, list) else None
    return record


def payload_hash(source: dict) -> str:
    """SHA-256 of the raw record (the same record always gives the same hash)."""
    text = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_records(
    conn, dataset: str, raw_table: str
) -> tuple[list[dict], list[dict], list[dict]]:
    """One raw table as ``(decoded records, source columns as stored, lineage)``, row by row."""
    sources, lineages = [], []
    for raw_row in read_raw_table(conn, raw_table):
        source, lineage = split_raw_row(raw_row)
        sources.append(source)
        lineages.append(lineage)
    return [decode_record(dataset, source) for source in sources], sources, lineages


def reference_context(conn) -> Context:
    """What is known about trainings, participants and enrollments, from every raw table so far."""
    history = {}
    for dataset in REFERENCE_DATASETS:
        history[dataset] = []
        for entry in loaded_raw_tables(
            conn, dataset
        ):  # oldest first, so the newest record wins
            history[dataset] += read_records(conn, dataset, entry["raw_table"])[0]
    return build_context(history)


# --------------------------------------------------------------------------------------
# Curating one raw table
# --------------------------------------------------------------------------------------
def enrich_records(
    dataset: str, records: list[dict], lineages: list[dict], ctx: Context
) -> list[tuple[dict, list]]:
    """Apply the cleaning rules of the dataset. Returns ``(curated columns, issues)`` per record."""
    if dataset != COACH_DATASET:
        enrich = ENRICHERS[dataset]
        return [enrich(record, ctx) for record in records]

    # The coach list is matched to enrollments; the e-mail counts only concern this workbook.
    coach_ctx = replace(
        ctx,
        coach_email_counts=build_context({COACH_DATASET: records}).coach_email_counts,
    )
    return curate_coach_rows(
        records,
        [lineage["_source_row_number"] for lineage in lineages],
        coach_ctx,
        source_file_name=lineages[0]["_source_object"],
        snapshot_date=lineages[0]["_snapshot_date"],
    )


def quality_flags(
    source_id, id_counts: Counter, payload: str, payload_counts: Counter, issues: list
) -> list[str]:
    """Names of everything noteworthy about one row: duplicates first, then the issues found while cleaning."""
    flags = []
    if source_id is not None and id_counts[source_id] > 1:
        flags.append("duplicate_source_id")
    if payload_counts[payload] > 1:
        flags.append("exact_duplicate_source_payload")
    for issue in issues:
        if issue.rule not in flags:
            flags.append(issue.rule)
    return flags


def lineage_columns(
    dataset: str, raw_table: str, lineage: dict, run_id: str, curated_at: datetime
) -> dict:
    """Where a curated row came from: the raw table, the raw load and this curation run."""
    return {
        "run_id": run_id,
        "curated_at": curated_at,
        "raw_table": raw_table,
        "raw_run_id": lineage["_run_id"],
        "raw_ingested_at": lineage["_ingested_at"],
        "snapshot_date": lineage["_snapshot_date"],
        "source_system": SOURCE_SYSTEMS[dataset],
        "source_object": lineage["_source_object"],
        "source_as_of_date": parse_iso_date(lineage.get("_source_as_of")),
        "source_row_number": lineage["_source_row_number"],
    }


def curate_batch(
    dataset: str,
    raw_table: str,
    records,
    sources,
    lineages,
    ctx: Context,
    *,
    run_id,
    curated_at,
):
    """Curate one raw table. Returns ``(curated rows, number of rows per validation status)``."""
    id_field = DATASETS[dataset].id_field
    payloads = [payload_hash(source) for source in sources]
    payload_counts = Counter(payloads)
    ids = [id_str(record.get(id_field)) if id_field else None for record in records]
    id_counts = Counter(source_id for source_id in ids if source_id is not None)

    rows, status_counts = [], Counter()
    for i, (columns, issues) in enumerate(
        enrich_records(dataset, records, lineages, ctx)
    ):
        if id_field and ids[i] is None:
            issues = [
                *issues,
                error(
                    "missing_source_id", "missing_id", f"{id_field} is missing or empty"
                ),
            ]
        status = validation_status(issues)
        status_counts[status] += 1
        rows.append(
            {
                **columns,
                **lineage_columns(dataset, raw_table, lineages[i], run_id, curated_at),
                "payload_hash": payloads[i],
                "source_id_duplicate_count": (
                    id_counts[ids[i]] if ids[i] is not None else 1
                ),
                "payload_duplicate_count": payload_counts[payloads[i]],
                "quality_flags": quality_flags(
                    ids[i], id_counts, payloads[i], payload_counts, issues
                ),
                "validation_status": status,
                "is_quarantined": status == "quarantined",
            }
        )
    return rows, status_counts


def write_curated_table(conn, dataset: str, snapshot_date, rows: list[dict]) -> None:
    """Check the rows against the curated schema (raises if they do not fit), then store them."""
    columns = CURATED_COLUMNS[dataset]
    table = enforce_schema(rows, columns)
    target = curated_name(snapshot_date, dataset)
    conn.execute(create_table_sql(target, columns))
    conn.register("curated_batch", table)
    try:
        conn.execute(f"INSERT INTO {target} SELECT * FROM curated_batch")
    finally:
        conn.unregister("curated_batch")


# --------------------------------------------------------------------------------------
# The whole layer
# --------------------------------------------------------------------------------------
def drop_orphaned_curated_tables(conn) -> int:
    """Drop curated tables whose raw load no longer exists (a same-day rerun found nothing new)."""
    existing_raw = {
        (entry["snapshot_date"], entry["dataset"]) for entry in loaded_raw_tables(conn)
    }
    orphans = [key for key in curation_state(conn) if key not in existing_raw]
    for snapshot_date, dataset in orphans:
        drop_curated_table(conn, dataset, snapshot_date)
    return len(orphans)


def raw_tables_to_curate(conn, rebuild: bool) -> list[dict]:
    """Raw tables without a curated table, or whose curated table was built from an older raw load."""
    curated_from = curation_state(conn)
    return [
        entry
        for entry in loaded_raw_tables(conn)
        if rebuild
        or curated_from.get((entry["snapshot_date"], entry["dataset"]))
        != entry["run_id"]
    ]


def curate_all(
    conn, *, run_id: str, curated_at: datetime, rebuild: bool = False
) -> dict[str, int]:
    """Curate every pending raw table (call inside a transaction). Returns the row count per curated table."""
    dropped = drop_orphaned_curated_tables(conn)
    if dropped:
        logger.info("Dropped %s orphaned curated table(s)", dropped)

    pending = raw_tables_to_curate(conn, rebuild)
    if not pending:
        logger.info("Curated layer is up to date")
        return {}

    ctx = reference_context(conn)
    rows_written = {}
    for entry in pending:
        dataset, snapshot_date = entry["dataset"], entry["snapshot_date"]
        records, sources, lineages = read_records(conn, dataset, entry["raw_table"])
        rows, status_counts = curate_batch(
            dataset,
            entry["raw_table"],
            records,
            sources,
            lineages,
            ctx,
            run_id=run_id,
            curated_at=curated_at,
        )
        write_curated_table(conn, dataset, snapshot_date, rows)

        name = table_name(snapshot_date, dataset)
        log_curation(
            conn,
            {
                "snapshot_date": snapshot_date,
                "dataset": dataset,
                "raw_table": entry["raw_table"],
                "raw_run_id": entry["run_id"],
                "curated_table": name,
                "run_id": run_id,
                "row_count": len(rows),
                "valid_count": status_counts["valid"],
                "review_count": status_counts["review"],
                "quarantined_count": status_counts["quarantined"],
                "curated_at": curated_at,
            },
        )
        rows_written[f"{SCHEMA_CURATED}.{name}"] = len(rows)
        logger.info(
            'Curated %s."%s": rows=%s valid=%s review=%s quarantined=%s',
            SCHEMA_CURATED,
            name,
            len(rows),
            status_counts["valid"],
            status_counts["review"],
            status_counts["quarantined"],
        )
    return rows_written
