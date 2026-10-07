import hashlib
from datetime import datetime, timezone

from .database import to_text
from .extraction_types import Dataset, DatasetResult, ExtractionError
from .logging_utils import get_logger

UTC = timezone.utc
logger = get_logger(__name__)


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------
def as_comparable_time(text) -> datetime | None:
    """Turn an ISO timestamp into something that can be compared; ``None`` if it is not one."""
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(
            text.strip().replace("Z", "+00:00").replace("z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def latest_time(values) -> str | None:
    """The newest of several ISO timestamps, as written. Values that are not timestamps are ignored."""
    candidates = [(as_comparable_time(value), value) for value in values]
    candidates = [(time, value) for time, value in candidates if time is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


def high_watermark_of(previous_loads: list[dict]) -> str | None:
    return latest_time(load["high_watermark"] for load in previous_loads)


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def row_signature(columns: list[str], record: dict) -> tuple:
    """A record as sorted (column, text) pairs, to compare it with an already stored row."""
    return tuple(sorted((column, to_text(record.get(column))) for column in columns))


def validate_columns(dataset: Dataset, columns: set[str]) -> None:
    """Fail the dataset when the source columns differ from the configured contract.

    Missing or unexpected columns are not silently ignored: the contract in ``Dataset.fields``,
    the raw schema handling and the curated mapping must be updated together first.
    """
    expected = set(dataset.fields)
    missing = sorted(expected - columns)
    unexpected = sorted(columns - expected)
    if not (missing or unexpected):
        return

    problems = []
    if missing:
        problems.append(f"missing columns: {missing}")
    if unexpected:
        problems.append(f"unexpected columns: {unexpected}")
    raise ExtractionError(
        f"{dataset.name}: source columns do not match the configured contract ({'; '.join(problems)})"
    )


def extraction_run_error(results: list[DatasetResult]) -> tuple[str | None, str | None]:
    """Return the pipeline-level error type and message for failed dataset extractions."""

    failed = [result for result in results if result.status == "FAILED"]

    if not failed:
        return None, None

    error_type = (
        failed[0].error_type if len(failed) == 1 else "MultipleExtractionErrors"
    )

    lines = [
        f"{result.dataset}: {result.error_type}: {result.error_message}"
        for result in failed
    ]

    error_message = (
        f"{len(failed)} of {len(results)} dataset extraction(s) failed. "
        + " | ".join(lines)
    )

    return error_type, error_message
