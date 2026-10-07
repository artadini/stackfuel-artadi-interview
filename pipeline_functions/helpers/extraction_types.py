from dataclasses import dataclass, field
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Exceptions
# Errors raised when extraction, API access, pagination, or schema validation fails.
# ---------------------------------------------------------------------------


class ExtractionError(RuntimeError):
    """The source is structurally unusable (for example, an unreadable workbook)."""


class ApiError(RuntimeError):
    """The API answered with an error that retrying will not fix."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class PaginationError(RuntimeError):
    """The pages of one read do not add up (for example, a repeated token)."""


class TemporaryFailure(Exception):
    """One attempt failed in a way that may be worth retrying."""

    def __init__(self, reason: str, retry_after: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class SchemaViolation(ValueError):
    """Rows do not fit the curated schema; this is a transformation issue."""


# ---------------------------------------------------------------------------
# Run and extraction results
# Data describing a pipeline run and the records returned by a source read.
# ---------------------------------------------------------------------------


@dataclass
class RunInfo:
    """Metadata shared by records extracted during one run."""

    run_id: str
    snapshot_date: date
    ingested_at: datetime
    page_size: int


@dataclass
class DatasetResult:
    dataset: str
    status: str  # LOADED | NO_CHANGES | FAILED
    load_mode: str
    records: list[dict] = field(default_factory=list)
    lineage: list[dict] = field(default_factory=list)

    extract_from: str | None = None
    high_watermark: str | None = None
    fingerprint: str | None = None

    records_fetched: int = 0
    boundary_rows_skipped: int = 0
    pages: int | None = None
    http_requests: int | None = None
    http_retries: int | None = None

    error_type: str | None = None
    error_message: str | None = None

    def summary(self) -> dict:
        """Compact, log-safe representation of one dataset extraction."""

        return {
            "dataset": self.dataset,
            "status": self.status,
            "load_mode": self.load_mode,
            "records_fetched": self.records_fetched,
            "rows_stored": len(self.records),
            "boundary_rows_skipped": self.boundary_rows_skipped,
            "high_watermark": self.high_watermark,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass
class Extraction:
    """Result of one paginated read, including the page for each record."""

    records: list[dict] = field(default_factory=list)
    page_numbers: list[int] = field(default_factory=list)
    total_count: int = 0
    pages: int = 0  # Number of requests, including the request for an empty result


# ---------------------------------------------------------------------------
# Dataset configuration
# Describes a dataset's source, extraction method, and expected source fields.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dataset:
    name: str  # Dataset part of the table name
    source: str  # Source part of the table name: crm | lxp | catalog | excel
    source_system: str  # Determines how source timestamps are interpreted
    kind: str  # api_incremental | api_snapshot | workbook
    fields: tuple[str, ...]  # Source fields known to the pipeline
    endpoint: str | None = None
    id_field: str | None = None
    filter_param: str | None = None  # Inclusive API filter parameter (>=)
    watermark_field: str | None = None  # Source field used for the high watermark


# ---------------------------------------------------------------------------
# Matching models
# Results and lookup context for matching participants, training, and enrollment.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    """Outcome of matching a source record to a participant and enrollment."""

    status: str  # matched | ambiguous | unmatched
    method: str  # Matching method or reason
    participant_id: str | None = None
    enrollment_id: str | None = None
    candidate_count: int = 0  # Number of possible participants
    enrollment_candidate_count: int = 0  # Number of possible enrollments
    enrollment_status: str = "unmatched"  # matched | ambiguous | unmatched


@dataclass
class Context:
    """Indexes and lookup data used during participant and enrollment matching."""

    participants: dict = field(default_factory=dict)  # ID -> participant details
    email_to_ids: dict = field(
        default_factory=dict
    )  # Normalized email -> participant IDs
    name_to_ids: dict = field(default_factory=dict)  # Match key -> participant IDs
    training_ids: set = field(default_factory=set)
    module_training: dict = field(default_factory=dict)  # Module ID -> training ID
    enrollments: dict = field(default_factory=dict)  # ID -> enrollment details
    participant_enrollments: dict = field(
        default_factory=dict
    )  # Participant ID -> enrollment IDs
    coach_email_counts: dict = field(
        default_factory=dict
    )  # Normalized email -> row count


@dataclass(frozen=True)
class TrainingMatch:
    """Outcome of matching a source value to a training."""

    training_id: str | None
    status: str  # matched | ambiguous | unmatched | missing
    candidate_ids: tuple[str, ...]
    normalized_spelling: str | None


# ---------------------------------------------------------------------------
# Validation and timestamp results
# Structured results from data-quality checks and timestamp parsing.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """A reported validation or data-quality issue."""

    rule: str
    severity: str
    error_type: str
    message: str


@dataclass(frozen=True)
class TimestampResult:
    """Parsed timestamp values and the outcome of parsing."""

    source_value: datetime | None  # For example, naive local time or aware UTC time
    utc_value: datetime | None
    status: str  # parsed | missing | invalid
    message: str | None = None
