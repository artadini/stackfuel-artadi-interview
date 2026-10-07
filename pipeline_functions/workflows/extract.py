"""Extract: source -> ``stackfuel.raw`` (a 1:1 copy, no transformation).

Per dataset the extraction chooses between

* **incremental** (CRM / LXP): ask the API only for records changed since the *high watermark*,
  the newest ``modified_at`` / ``event_time`` / ``submitted_at`` of the earlier loads, written
  exactly as the source wrote it. The first load has no watermark and reads everything.
  The API filter includes the watermark itself, so the newest stored rows come back once more;
  they are recognised by their content and not stored twice.
* **snapshot** (course catalog, coach workbook): there is no change filter, so the whole source
  is read and only stored when its content differs from the previous load.

Reading never writes. ``extract_all`` returns plain results; ``load_raw`` stores them (call it
inside a transaction), so a failed run leaves nothing behind and never moves a watermark.
"""

import json
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from ..helpers.extraction_types import (
    ExtractionError,
    RunInfo,
    DatasetResult,
)
from ..helpers.api_client import ApiClient
from ..helpers.config import (
    COACH_SHEET_NAME,
    COACH_SOURCE_AS_OF,
    DATASETS,
    Dataset,
    table_name,
)
from ..helpers.database import (
    drop_raw_table,
    loaded_on,
    log_extraction,
    previous_extractions,
    stored_row_count,
    stored_rows_at_watermark,
    write_raw_table,
)
from ..helpers.extraction_utils import (
    as_comparable_time,
    latest_time,
    high_watermark_of,
    sha256_of,
    row_signature,
    validate_columns,
    extraction_run_error,
)
from ..helpers.logging_utils import get_logger

UTC = timezone.utc
logger = get_logger(__name__)

# A complete read that returns less than this share of the rows already stored is not believed.
MIN_SHARE_OF_STORED_ROWS = 0.5


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def lineage_for(
    run: RunInfo, source_object: str, source_as_of, page_numbers, row_numbers
) -> list[dict]:
    """The ``_``-prefixed lineage values of each stored record."""
    return [
        {
            "_run_id": run.run_id,
            "_ingested_at": run.ingested_at,
            "_snapshot_date": run.snapshot_date,
            "_source_object": source_object,
            "_source_as_of": source_as_of,
            "_page_number": page,
            "_source_row_number": row,
        }
        for page, row in zip(page_numbers, row_numbers, strict=True)
    ]


# --------------------------------------------------------------------------------------
# Coach workbook
# --------------------------------------------------------------------------------------
def is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def open_workbook(path: Path):
    if not path.is_file():
        raise ExtractionError(f"Coach workbook not found: {path}")
    try:
        return load_workbook(path, read_only=True, data_only=False)
    except (
        InvalidFileException,
        zipfile.BadZipFile,
        OSError,
        KeyError,
        ValueError,
    ) as error:
        raise ExtractionError(
            f"Coach workbook is unreadable: {path} ({error})"
        ) from error


def read_headers(dataset: Dataset, header_row) -> list[str | None]:
    """Column names of the sheet. Rejects missing, duplicate, or unexpected headers."""
    if header_row is None or all(is_blank(cell) for cell in header_row):
        raise ExtractionError("Coach workbook sheet has no header row")

    headers = [None if is_blank(cell) else str(cell).strip() for cell in header_row]
    names = [header for header in headers if header]

    if len(set(names)) != len(names):
        raise ExtractionError("Coach workbook has duplicate column headers")

    validate_columns(dataset, set(names))
    return headers


def read_data_rows(
    rows, headers: list[str | None]
) -> tuple[list[dict], list[int], int]:
    """The non-empty rows below the header as records, their sheet row numbers, and the count of skipped empty rows."""
    records, row_numbers, empty_rows = [], [], 0
    for row_number, cells in enumerate(rows, start=2):  # row 1 is the header
        if all(is_blank(cell) for cell in cells):
            empty_rows += 1
            continue
        record = {}
        for column_number, (header, cell) in enumerate(zip(headers, cells), start=1):
            if header:
                record[header] = cell
            elif not is_blank(cell):
                record[f"unnamed_column_{column_number}"] = cell
        records.append(record)
        row_numbers.append(row_number)
    return records, row_numbers, empty_rows


def read_workbook(dataset: Dataset, path: Path) -> tuple[list[dict], list[int], int]:
    """Rows of the ``Betreuung`` sheet exactly as stored: ``(records, sheet row numbers, skipped empty rows)``.

    Cell values are not interpreted. Completely empty rows carry no record and are skipped.
    """
    workbook = open_workbook(path)
    try:
        if COACH_SHEET_NAME not in workbook.sheetnames:
            raise ExtractionError(f"Coach workbook has no '{COACH_SHEET_NAME}' sheet")

        rows = workbook[COACH_SHEET_NAME].iter_rows(values_only=True)
        headers = read_headers(dataset, next(rows, None))
        return read_data_rows(rows, headers)
    finally:
        workbook.close()


# --------------------------------------------------------------------------------------
# One dataset
# --------------------------------------------------------------------------------------
def positions_to_store(
    conn, dataset: Dataset, run: RunInfo, watermark: str | None, records: list[dict]
) -> list[int]:
    """Positions of the records that are not already stored.

    Only records sitting exactly on the watermark can be re-deliveries; a record that is new or
    changed (even with the same timestamp) differs from every stored row and is kept.
    """
    everything = list(range(len(records)))
    if not watermark or not records:
        return everything
    stored = stored_rows_at_watermark(
        conn, dataset.name, run.snapshot_date, dataset.watermark_field, watermark
    )
    if not stored:
        return everything
    columns = [column for column, _ in next(iter(stored))]
    return [
        i
        for i, record in enumerate(records)
        if row_signature(columns, record) not in stored
    ]


def warn_about_unreadable_times(dataset: Dataset, records: list[dict]) -> None:
    values = [record.get(dataset.watermark_field) for record in records]
    unreadable = [
        value
        for value in values
        if value is not None and as_comparable_time(value) is None
    ]
    if unreadable:
        logger.warning(
            "%s: %s record(s) have a %s that is not an ISO timestamp; they cannot move the watermark",
            dataset.name,
            len(unreadable),
            dataset.watermark_field,
        )


def refuse_shrunken_source(
    conn, dataset: Dataset, run: RunInfo, fetched_count: int
) -> None:
    """Stop a complete read that returns far fewer records than were stored before.

    Without this, a stale or wrong API (for example an old server that is still running on the
    port) would silently replace a good same-day table with a handful of rows, and every
    downstream model would be built on it.
    """
    stored = stored_row_count(conn, dataset.name, run.snapshot_date)
    if stored and fetched_count < stored * MIN_SHARE_OF_STORED_ROWS:
        raise ExtractionError(
            f"{dataset.name}: the source returned {fetched_count} records for a complete read, but "
            f"{stored} rows are already stored. This usually means a stale or wrong API answers on the "
            f"configured URL. Nothing was written. If the source really shrank, rerun with "
            f"--allow-shrinking-source."
        )


def extract_incremental(
    conn,
    client: ApiClient,
    dataset: Dataset,
    previous_loads: list[dict],
    run: RunInfo,
    full_refresh: bool,
    allow_shrinking_source: bool = False,
) -> DatasetResult:
    watermark = None if full_refresh else high_watermark_of(previous_loads)
    load_mode = "incremental" if watermark else "full"
    filters = {dataset.filter_param: watermark} if watermark else {}

    fetched = client.fetch_all(dataset.endpoint, filters, run.page_size)
    records = fetched.records
    if load_mode == "full" and not allow_shrinking_source:
        refuse_shrunken_source(conn, dataset, run, len(records))

    if records:
        columns = set().union(*(record.keys() for record in records))
        validate_columns(dataset, columns)

    keep = positions_to_store(conn, dataset, run, watermark, records)

    newest = latest_time(record.get(dataset.watermark_field) for record in records)
    # An incremental read can only move the watermark forward; a full read is the truth.
    high_watermark = (
        latest_time([watermark, newest]) if load_mode == "incremental" else newest
    )
    warn_about_unreadable_times(dataset, records)

    return DatasetResult(
        dataset.name,
        status="LOADED" if keep else "NO_CHANGES",
        load_mode=load_mode,
        records=[records[i] for i in keep],
        lineage=lineage_for(
            run,
            dataset.endpoint,
            None,
            page_numbers=[fetched.page_numbers[i] for i in keep],
            row_numbers=[i + 1 for i in keep],
        ),
        extract_from=watermark,
        high_watermark=high_watermark,
        records_fetched=len(records),
        boundary_rows_skipped=len(records) - len(keep),
        pages=fetched.pages,
    )


def extract_snapshot(
    client: ApiClient,
    dataset: Dataset,
    previous_loads: list[dict],
    run: RunInfo,
    full_refresh: bool,
) -> DatasetResult:
    response = client.get(dataset.endpoint)
    records = response.get("data") if isinstance(response, dict) else None
    if not isinstance(records, list) or any(
        not isinstance(record, dict) for record in records
    ):
        raise ExtractionError(
            f"{dataset.endpoint}: response has no list of objects under 'data'"
        )

    if records:
        columns = set().union(*(record.keys() for record in records))
        validate_columns(dataset, columns)

    fingerprint = sha256_of(
        json.dumps(
            records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    unchanged = (
        bool(previous_loads) and previous_loads[-1]["source_fingerprint"] == fingerprint
    )
    result = DatasetResult(
        dataset.name,
        "NO_CHANGES",
        "full",
        fingerprint=fingerprint,
        records_fetched=len(records),
        pages=1,
    )
    if (unchanged and not full_refresh) or not records:
        return result

    as_of = response.get("as_of")
    result.status = "LOADED"
    result.records = records
    result.lineage = lineage_for(
        run,
        dataset.endpoint,
        as_of if isinstance(as_of, str) else None,
        page_numbers=[1] * len(records),
        row_numbers=range(1, len(records) + 1),
    )
    return result


def extract_workbook(
    dataset: Dataset,
    previous_loads: list[dict],
    run: RunInfo,
    path: Path,
    full_refresh: bool,
) -> DatasetResult:
    records, row_numbers, empty_rows = read_workbook(dataset, path)
    fingerprint = sha256_of(path.read_bytes())
    unchanged = (
        bool(previous_loads) and previous_loads[-1]["source_fingerprint"] == fingerprint
    )
    result = DatasetResult(
        dataset.name,
        "NO_CHANGES",
        "full",
        fingerprint=fingerprint,
        records_fetched=len(records),
        pages=1,
    )
    if (unchanged and not full_refresh) or not records:
        return result

    if empty_rows:
        logger.info("Coach workbook: %s empty row(s) skipped", empty_rows)
    result.status = "LOADED"
    result.records = records
    result.lineage = lineage_for(
        run,
        path.name,
        COACH_SOURCE_AS_OF.isoformat(),
        page_numbers=[None] * len(records),
        row_numbers=row_numbers,
    )
    return result


# --------------------------------------------------------------------------------------
# All datasets
# --------------------------------------------------------------------------------------
def extract_dataset(
    conn,
    client,
    dataset: Dataset,
    coach_file: Path,
    run: RunInfo,
    full_refresh: bool,
    allow_shrinking_source: bool = False,
) -> DatasetResult:
    previous_loads = previous_extractions(conn, dataset.name, run.snapshot_date)
    if dataset.kind == "api_incremental":
        result = extract_incremental(
            conn,
            client,
            dataset,
            previous_loads,
            run,
            full_refresh,
            allow_shrinking_source,
        )
    elif dataset.kind == "api_snapshot":
        result = extract_snapshot(client, dataset, previous_loads, run, full_refresh)
    else:
        result = extract_workbook(
            dataset, previous_loads, run, coach_file, full_refresh
        )
    # A load without new rows keeps the watermark it had.
    if result.status == "NO_CHANGES" and result.high_watermark is None:
        result.high_watermark = high_watermark_of(previous_loads)
    return result


def extract_all(
    conn,
    client: ApiClient,
    coach_file: Path,
    run: RunInfo,
    full_refresh: bool = False,
    allow_shrinking_source: bool = False,
) -> list[DatasetResult]:
    """Read every source and return one result per dataset, including failures."""
    results = []

    for dataset in DATASETS.values():
        calls_before = dict(client.stats)

        try:
            result = extract_dataset(
                conn,
                client,
                dataset,
                coach_file,
                run,
                full_refresh,
                allow_shrinking_source,
            )

        except ExtractionError as error:
            logger.error(
                "Extraction failed for %s: %s",
                dataset.name,
                error,
            )

            result = DatasetResult(
                dataset=dataset.name,
                status="FAILED",
                load_mode=(
                    "incremental" if dataset.kind == "api_incremental" else "full"
                ),
                error_type=type(error).__name__,
                error_message=str(error),
            )

        except Exception as error:
            logger.exception(
                "Unexpected extraction failure for %s",
                dataset.name,
            )

            result = DatasetResult(
                dataset=dataset.name,
                status="FAILED",
                load_mode=(
                    "incremental" if dataset.kind == "api_incremental" else "full"
                ),
                error_type=type(error).__name__,
                error_message=str(error),
            )

        if dataset.kind != "workbook":
            result.http_requests = client.stats["requests"] - calls_before["requests"]
            result.http_retries = client.stats["retries"] - calls_before["retries"]

        logger.info(
            "Extracted %s: status=%s mode=%s fetched=%s new=%s "
            "boundary_skipped=%s from=%s watermark=%s error=%s",
            dataset.name,
            result.status,
            result.load_mode,
            result.records_fetched,
            len(result.records),
            result.boundary_rows_skipped,
            result.extract_from,
            result.high_watermark,
            result.error_message,
        )

        results.append(result)

    return results


def load_raw(
    conn,
    results: list[DatasetResult],
    *,
    run_id: str,
    snapshot_date: date,
    extracted_at: datetime,
) -> dict:
    """Write raw tables and extraction metadata inside one transaction."""
    rows_written = {}

    for result in results:
        dataset = DATASETS[result.dataset]
        raw_table = None

        if result.status == "LOADED":
            write_raw_table(
                conn,
                result.dataset,
                snapshot_date,
                result.records,
                result.lineage,
            )
            raw_table = table_name(snapshot_date, result.dataset)
            rows_written[result.dataset] = len(result.records)

        elif result.status == "NO_CHANGES":
            # A successful no-change run replaces the same-day raw table
            # with no current rows, preserving the existing behavior.
            drop_raw_table(conn, result.dataset, snapshot_date)
            rows_written[result.dataset] = 0

        elif result.status == "FAILED":
            # Preserve the previous raw table.
            rows_written[result.dataset] = 0
            if loaded_on(conn, result.dataset, snapshot_date):
                # Replacing the log row would orphan today's raw table, and the curated layer would then
                # drop today's curated table. The failure stays visible in meta.pipeline_runs.
                logger.warning(
                    "%s failed; the load of %s is kept", result.dataset, snapshot_date
                )
                continue

        else:
            raise ValueError(f"Unsupported extraction status: {result.status}")

        log_extraction(
            conn,
            {
                "snapshot_date": snapshot_date,
                "dataset": result.dataset,
                "source": dataset.source,
                "run_id": run_id,
                "status": result.status,
                "load_mode": result.load_mode,
                "raw_table": raw_table,
                "extract_from": result.extract_from,
                "watermark_field": dataset.watermark_field,
                "high_watermark": result.high_watermark,
                "source_fingerprint": result.fingerprint,
                "records_fetched": result.records_fetched,
                "boundary_rows_skipped": result.boundary_rows_skipped,
                "row_count": len(result.records),
                "pages": result.pages,
                "http_requests": result.http_requests,
                "http_retries": result.http_retries,
                "extracted_at": extracted_at,
                "error_type": result.error_type,
                "error_message": result.error_message,
            },
        )

    return rows_written
