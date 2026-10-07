"""Command-line runner: extract into ``raw``, then curate into ``curated``.

Each layer is committed in its own transaction. Raw data therefore survives a curation problem,
and a failed layer leaves no half-written tables and no moved watermark behind.
"""

import argparse
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from ..helpers.api_client import ApiClient
from ..helpers.api_secrets import get_api_credentials
from ..helpers.config import API_BASE_URL, COACH_FILE, DATABASE_PATH, PAGE_SIZE
from ..helpers.database import connect, finish_run, start_run, transaction
from ..helpers.logging_utils import get_logger
from ..helpers.extraction_utils import extraction_run_error
from .curate import curate_all
from .extract import RunInfo, extract_all, load_raw

LAYERS = ("raw", "curated", "all")
logger = get_logger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_raw_layer(conn, client: ApiClient, args, run: RunInfo) -> tuple[dict, list]:
    """Read every source first, then store everything in one transaction.

    A dataset that cannot be read is recorded as FAILED and keeps its previous raw table; the
    other datasets are still loaded (the run then ends as PARTIAL_SUCCESS).
    """
    results = extract_all(
        conn,
        client,
        args.coach_file,
        run,
        args.full_refresh,
        getattr(args, "allow_shrinking_source", False),
    )

    with transaction(conn):
        rows_written = load_raw(
            conn,
            results,
            run_id=run.run_id,
            snapshot_date=run.snapshot_date,
            extracted_at=run.ingested_at,
        )
    return rows_written, results


def run_curated_layer(conn, args, run: RunInfo) -> dict:
    with transaction(conn):
        return curate_all(
            conn, run_id=run.run_id, curated_at=utc_now(), rebuild=args.rebuild_curated
        )


def run_pipeline(args, client: ApiClient | None = None) -> dict:
    """Run the requested layers and return how many rows each layer wrote."""
    started_at = utc_now()
    error_type = error_message = None
    snapshot_date = args.snapshot_date or started_at.date()
    run = RunInfo(str(uuid.uuid4()), snapshot_date, started_at, args.page_size)
    conn = connect(args.duckdb_path)
    start_run(
        conn, run.run_id, snapshot_date, started_at, args.layer, args.full_refresh
    )
    logger.info(
        "Run %s started: layer=%s snapshot_date=%s full_refresh=%s database=%s",
        run.run_id,
        args.layer,
        snapshot_date,
        args.full_refresh,
        args.duckdb_path,
    )
    summary = {
        "run_id": run.run_id,
        "snapshot_date": snapshot_date,
        "raw": {},
        "raw_results": [],
        "curated": {},
    }
    try:
        if args.layer in ("raw", "all"):
            client = client or ApiClient(args.base_url, *get_api_credentials())
            summary["raw"], summary["raw_results"] = run_raw_layer(
                conn, client, args, run
            )
            error_type, error_message = extraction_run_error(summary["raw_results"])

        if args.layer in ("curated", "all"):
            summary["curated"] = run_curated_layer(conn, args, run)

        finish_run(
            conn,
            run.run_id,
            utc_now(),
            "PARTIAL_SUCCESS" if error_type else "SUCCESS",
            error_type=error_type,
            error_message=error_message,
        )
    except Exception as error:
        finish_run(
            conn,
            run.run_id,
            utc_now(),
            "FAILED",
            error_type=type(error).__name__,
            error_message=str(error),
        )
        logger.exception("Run %s failed", run.run_id)
        raise
    finally:
        conn.close()

    raw_results = summary["raw_results"]

    logger.info(
        "Run %s finished: raw_loaded=%d/%d raw_failed=%s curated_rows=%d",
        run.run_id,
        sum(result.status == "LOADED" for result in raw_results),
        len(raw_results),
        [result.dataset for result in raw_results if result.status == "FAILED"],
        sum(summary["curated"].values()),
    )

    return summary


def parse_args(argv=None):
    env = os.environ.get
    parser = argparse.ArgumentParser(
        description="Stackfuel pipeline: source -> raw -> curated (DuckDB)"
    )
    parser.add_argument("--layer", choices=LAYERS, default=env("LAYER", "all"))
    parser.add_argument(
        "--snapshot-date",
        type=date.fromisoformat,
        default=(
            date.fromisoformat(env("SNAPSHOT_DATE")) if env("SNAPSHOT_DATE") else None
        ),
        help="date part of the table names, default: today (UTC); useful for backfills and tests",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        default=env("FULL_REFRESH", "").lower() in {"1", "true", "yes"},
        help="ignore watermarks and fingerprints: read every source completely",
    )
    parser.add_argument(
        "--rebuild-curated",
        action="store_true",
        default=env("REBUILD_CURATED", "").lower() in {"1", "true", "yes"},
        help="curate every raw table again, not only the new ones",
    )
    parser.add_argument(
        "--allow-shrinking-source",
        action="store_true",
        default=env("ALLOW_SHRINKING_SOURCE", "").lower() in {"1", "true", "yes"},
        help="accept a complete read that returns far fewer records than are already stored (default: stop the run)",
    )
    parser.add_argument("--base-url", default=env("API_BASE_URL", API_BASE_URL))
    parser.add_argument(
        "--duckdb-path", type=Path, default=Path(env("DUCKDB_PATH", DATABASE_PATH))
    )
    parser.add_argument(
        "--coach-file", type=Path, default=Path(env("COACH_FILE", COACH_FILE))
    )
    parser.add_argument(
        "--page-size", type=int, default=int(env("PAGE_SIZE", PAGE_SIZE))
    )
    return parser.parse_args(argv)


EXIT_FAILED = 1
EXIT_PARTIAL_SUCCESS = 2


def failed_datasets(summary: dict) -> list[str]:
    return [
        result.dataset for result in summary["raw_results"] if result.status == "FAILED"
    ]


def main(argv=None) -> None:
    """Exit codes: 0 = SUCCESS, 1 = FAILED, 2 = PARTIAL_SUCCESS (some datasets failed, the rest is loaded).

    A partial run is not a success for callers such as ``make demo``: they must not build reports
    on a load in which a dataset is missing or outdated.
    """
    args = parse_args(argv)
    try:
        summary = run_pipeline(args)
    except Exception as error:
        # run_pipeline logs failures after the run started; this also covers earlier ones (a locked database)
        logger.error("Pipeline failed: %s", error)
        raise SystemExit(EXIT_FAILED) from error

    failed = failed_datasets(summary)
    if failed:
        logger.error(
            "Pipeline finished as PARTIAL_SUCCESS: %s failed (see meta.extraction_log)",
            ", ".join(failed),
        )
        raise SystemExit(EXIT_PARTIAL_SUCCESS)
