"""Cleaning rules per dataset (raw -> curated).

Each ``enrich_*`` function takes one ``record`` (a raw row: source columns as text, only the
``modules`` of a training are already decoded) and the ``Context`` of reference data. It returns
``(columns, issues)``:

* ``columns``: the typed, standardized curated columns. The original text is kept in ``*_raw`` columns.
* ``issues``: what was noteworthy (see ``quarantine.py``). A value that cannot be read becomes NULL
  and gets an error issue. Nothing is dropped and nothing is silently converted.

The ``Context`` is built from the whole raw history of the reference datasets, so a small incremental
batch is still checked against every known participant, enrollment, training and module.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .config import REFERENCE_DATE, SOURCE_SYSTEMS, SOURCE_TIMEZONES, TIMESTAMP_SPECS
from .quarantine import Issue, error, warning
from .standardize import (
    ENROLLMENT_STATUSES,
    EVENT_TYPES,
    FUNDING_TYPES,
    NUMERIC_SURVEY_RANGES,
    OPEN_ENROLLMENT_STATUSES,
    SURVEY_QUESTIONS,
    SURVEY_TYPES,
    TRAINING_VARIANTS,
    UNKNOWN,
    clean_text,
    is_missing,
    is_valid_email,
    name_match_key,
    nps_category,
    normalize_city,
    normalize_email,
    normalize_federal_state,
    normalize_person_name,
    normalize_upper,
    parse_answer,
    parse_boolean,
    parse_decimal,
    parse_flexible_date,
    standardize_category,
)
from .timestamps import BERLIN, parse_timestamp

from ..helpers.extraction_types import (
    Context,
)

EARLIEST_PLAUSIBLE_BIRTH_DATE = date(1900, 1, 1)
MAX_MONEY = Decimal("9999999999")  # largest value of DECIMAL(12,2)
MAX_INTEGER = 2**31 - 1  # largest value of INTEGER
MAX_SCORE = 1000  # DECIMAL(5,2) holds scores below 1000
_WHITESPACE = re.compile(r"\s+")


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------
def id_str(value) -> str | None:
    """IDs are VARCHAR in the curated layer (trimmed text); an empty ID is ``None``."""
    text = None if value is None else str(value).strip()
    return text or None


def raw_text(value) -> str | None:
    """The original text of a value (``None`` stays ``None``)."""
    return None if value is None else str(value)


def _whitespace_changed(value) -> bool:
    return isinstance(value, str) and _WHITESPACE.sub(" ", value).strip() != value


def _category(
    columns: dict,
    issues: list[Issue],
    record: dict,
    name: str,
    allowed: tuple[str, ...] | None,
) -> str:
    """Standardize a category into ``columns[name]`` and keep the original in ``columns[name_raw]``.

    Returns the status: ``mapped``, ``missing`` or ``unmapped``.
    """
    original = record.get(name)
    value, status = standardize_category(original, allowed)
    columns[name] = value
    columns[f"{name}_raw"] = raw_text(original)
    if status == "unmapped":
        issues.append(
            error(
                f"invalid_{name}",
                "invalid_category",
                f"{name}={original!r} is not a known value",
            )
        )
    elif status == "missing":
        issues.append(warning(f"missing_{name}"))
    return status


def _date(columns: dict, issues: list[Issue], record: dict, name: str) -> date | None:
    """Parse a date into ``columns[name]`` and keep the original in ``columns[name_raw]``."""
    original = record.get(name)
    value, status = parse_flexible_date(original)
    columns[name] = value
    columns[f"{name}_raw"] = raw_text(original)
    if status == "invalid":
        issues.append(
            error(
                f"invalid_{name}", "invalid_date", f"{name}={original!r} is not a date"
            )
        )
    elif status == "missing":
        issues.append(warning(f"missing_{name}"))
    return value


def _money(value) -> tuple[Decimal | None, str]:
    """``(amount with 2 decimals, status)``; an amount that does not fit DECIMAL(12,2) is invalid."""
    amount, status = parse_decimal(value)
    if amount is None:
        return None, status
    if abs(amount) > MAX_MONEY:
        return None, "invalid"
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), status


def _whole_number(value) -> tuple[int | None, str]:
    """``(integer, status)``; a fraction or a value that does not fit INTEGER is invalid."""
    number, status = parse_decimal(value)
    if number is None:
        return None, status
    if number != number.to_integral_value() or abs(number) > MAX_INTEGER:
        return None, "invalid"
    return int(number), "parsed"


def apply_timestamps(
    dataset: str, record: dict, columns: dict, issues: list[Issue]
) -> None:
    """Add the source value, the original text and the UTC value of every timestamp, and the time zone."""
    source_system = SOURCE_SYSTEMS[dataset]
    for field_name, local_column, raw_column, utc_column in TIMESTAMP_SPECS.get(
        dataset, []
    ):
        original = record.get(field_name)
        result = parse_timestamp(original, source_system)
        columns[raw_column] = raw_text(original)
        columns[local_column] = result.source_value
        columns[utc_column] = result.utc_value
        if result.status == "invalid":
            issues.append(
                error(
                    f"invalid_{field_name}",
                    "invalid_timestamp",
                    f"{field_name}={original!r}: {result.message}",
                )
            )
        elif result.status == "missing":
            issues.append(warning(f"missing_{field_name}"))
    columns["source_timezone"] = SOURCE_TIMEZONES.get(dataset)


# --------------------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------------------
def participant_keys(record: dict) -> tuple[str | None, str | None]:
    """``(normalized e-mail, name match key)`` of a participant record."""
    return normalize_email(record.get("email")), name_match_key(
        record.get("first_name"), record.get("last_name")
    )


def _known_participants(records: list[dict]) -> dict:
    participants = {}
    for record in records:
        email, name_key = participant_keys(record)
        participants[id_str(record.get("participant_id"))] = {
            "email": email,
            "name_key": name_key,
        }
    participants.pop(None, None)
    return participants


def _known_enrollments(records: list[dict]) -> dict:
    enrollments = {}
    for record in records:
        cohort_start, _ = parse_flexible_date(record.get("cohort_start_date"))
        enrollments[id_str(record.get("enrollment_id"))] = {
            "participant_id": id_str(record.get("participant_id")),
            "training_id": normalize_upper(record.get("training_id")),
            "cohort_start": cohort_start,
        }
    enrollments.pop(None, None)
    return enrollments


def build_context(datasets: dict[str, list[dict]]) -> Context:
    """Reference data from raw records: ``{dataset: [record, ...]}``, oldest record first.

    For an entity delivered several times the last record wins, which is the newest state because
    raw tables are read in snapshot-date order.
    """
    ctx = Context()

    ctx.participants = _known_participants(datasets.get("participants", []))
    by_email, by_name = defaultdict(set), defaultdict(set)
    for participant_id, person in ctx.participants.items():
        if person["email"]:
            by_email[person["email"]].add(participant_id)
        if person["name_key"]:
            by_name[person["name_key"]].add(participant_id)
    ctx.email_to_ids, ctx.name_to_ids = dict(by_email), dict(by_name)

    for record in datasets.get("trainings", []):
        training_id = normalize_upper(record.get("training_id"))
        ctx.training_ids.add(training_id)
        for module in record.get("modules") or []:
            ctx.module_training[normalize_upper(module.get("module_id"))] = training_id

    ctx.enrollments = _known_enrollments(datasets.get("enrollments", []))
    by_participant = defaultdict(list)
    for enrollment_id, enrollment in ctx.enrollments.items():
        by_participant[enrollment["participant_id"]].append(enrollment_id)
    ctx.participant_enrollments = dict(by_participant)

    for record in datasets.get("coach_betreuungsliste", []):
        email = normalize_email(record.get("E-Mail"))
        if email:
            ctx.coach_email_counts[email] = ctx.coach_email_counts.get(email, 0) + 1
    return ctx


# --------------------------------------------------------------------------------------
# Trainings
# --------------------------------------------------------------------------------------
def _modules_are_consistent(modules, training_id: str | None) -> bool:
    """Module orders are 1..n, module IDs are unique, and each ID starts with the training ID."""
    well_formed = isinstance(modules, list) and all(
        isinstance(module, dict) and isinstance(module.get("module_order"), int)
        for module in modules
    )
    if not well_formed or not modules:
        return False
    orders = sorted(module["module_order"] for module in modules)
    module_ids = [normalize_upper(module.get("module_id")) for module in modules]
    return (
        orders == list(range(1, len(modules) + 1))
        and len(set(module_ids)) == len(module_ids)
        and all(
            module_id and training_id and module_id.startswith(f"{training_id}-")
            for module_id in module_ids
        )
    )


def enrich_trainings(record: dict, ctx: Context) -> tuple[dict, list[Issue]]:
    issues: list[Issue] = []
    columns = {
        "training_id": normalize_upper(record.get("training_id")),
        "training_id_raw": raw_text(record.get("training_id")),
    }
    for name in ("training_name", "track"):
        columns[name] = clean_text(record.get(name)) or UNKNOWN
        columns[f"{name}_raw"] = raw_text(record.get(name))

    variant_status = _category(columns, issues, record, "variant", TRAINING_VARIANTS)
    columns["is_variant_valid"] = (
        None if variant_status == "missing" else variant_status == "mapped"
    )

    columns["duration_weeks"], weeks_status = _whole_number(
        record.get("duration_weeks")
    )
    if weeks_status == "invalid":
        issues.append(
            error(
                "invalid_duration_weeks",
                "invalid_number",
                f"duration_weeks={record.get('duration_weeks')!r}",
            )
        )
    columns["list_price_eur"], price_status = _money(record.get("list_price_eur"))
    if price_status == "invalid":
        issues.append(
            error(
                "invalid_list_price_eur",
                "invalid_number",
                f"list_price_eur={record.get('list_price_eur')!r}",
            )
        )

    columns["is_active"], columns["is_active_status"] = parse_boolean(
        record.get("is_active")
    )
    if columns["is_active_status"] == "invalid":
        issues.append(
            error(
                "invalid_is_active",
                "invalid_boolean",
                f"is_active={record.get('is_active')!r}",
            )
        )
    elif columns["is_active_status"] == "missing":
        issues.append(warning("missing_is_active"))

    # A structural check only: no module counts, hours or progress are calculated here.
    columns["has_consistent_modules"] = _modules_are_consistent(
        record.get("modules"), columns["training_id"]
    )
    if not columns["has_consistent_modules"]:
        issues.append(warning("inconsistent_training_modules"))
    # The complete source object including the nested `modules` list, as JSON text.
    columns["payload_json"] = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return columns, issues


# --------------------------------------------------------------------------------------
# Participants
# --------------------------------------------------------------------------------------
def _name_columns(record: dict, issues: list[Issue]) -> dict:
    columns = {}
    for name in ("first_name", "last_name"):
        columns[name] = normalize_person_name(record.get(name)) or UNKNOWN
        columns[f"{name}_raw"] = raw_text(record.get(name))
        if columns[name] == UNKNOWN:
            issues.append(warning(f"missing_{name}"))
    columns["name_match_key"] = name_match_key(
        record.get("first_name"), record.get("last_name")
    )
    return columns


def _email_columns(record: dict, ctx: Context, issues: list[Issue]) -> dict:
    original = record.get("email")
    email = normalize_email(original)
    is_valid = is_valid_email(email)
    shared_by = len(ctx.email_to_ids.get(email, ())) if email else 0
    if email is None:
        issues.append(warning("missing_email"))
    else:
        if email != original:
            issues.append(warning("email_normalization_applied"))
        if shared_by > 1:
            issues.append(warning("normalized_email_collision"))
        if is_valid is False:
            issues.append(warning("invalid_email_format"))
    return {
        "email": email,
        "email_raw": raw_text(original),
        "is_email_valid": is_valid,
        "email_match_count": shared_by,
    }


def _birth_date_columns(record: dict, issues: list[Issue]) -> dict:
    original = record.get("birth_date")
    birth_date, status = parse_flexible_date(original)
    if status == "invalid":
        issues.append(
            error(
                "invalid_birth_date",
                "invalid_date",
                f"birth_date={original!r} is not a date",
            )
        )
        is_plausible = False
    elif status == "parsed":
        is_plausible = EARLIEST_PLAUSIBLE_BIRTH_DATE <= birth_date <= REFERENCE_DATE
        if not is_plausible:
            issues.append(warning("implausible_birth_date", f"birth_date={original!r}"))
    else:
        is_plausible = None
    return {
        "birth_date": birth_date,
        "birth_date_raw": raw_text(original),
        "is_birth_date_valid": is_plausible,
    }


def _location_columns(record: dict, issues: list[Issue]) -> dict:
    city = normalize_city(record.get("city"))
    if city is None:
        issues.append(warning("missing_city"))
    state, state_status = normalize_federal_state(record.get("federal_state"))
    if state_status == "unmapped":
        issues.append(
            error(
                "invalid_federal_state",
                "invalid_category",
                f"federal_state={record.get('federal_state')!r}",
            )
        )
    elif state_status == "missing":
        issues.append(warning("missing_federal_state"))
    return {
        "city": city or UNKNOWN,
        "city_raw": raw_text(record.get("city")),
        "federal_state": state,
        "federal_state_raw": raw_text(record.get("federal_state")),
    }


def enrich_participants(record: dict, ctx: Context) -> tuple[dict, list[Issue]]:
    issues: list[Issue] = []
    columns = {"participant_id": id_str(record.get("participant_id"))}
    columns |= _name_columns(record, issues)
    columns |= _email_columns(record, ctx, issues)
    columns |= _birth_date_columns(record, issues)
    columns |= _location_columns(record, issues)
    _category(columns, issues, record, "funding_type", FUNDING_TYPES)
    _category(columns, issues, record, "acquisition_channel", None)

    text_fields = (
        "first_name",
        "last_name",
        "city",
        "email",
        "federal_state",
        "funding_type",
        "acquisition_channel",
    )
    if any(_whitespace_changed(record.get(name)) for name in text_fields):
        issues.append(warning("text_whitespace_normalized"))

    # Participants sharing an e-mail are probably one person; the lowest ID is the hint, nothing is merged.
    same_person = ctx.email_to_ids.get(columns["email"], set()) | {
        columns["participant_id"]
    }
    columns["canonical_participant_id"] = min(
        same_person, key=lambda value: (len(value), value)
    )
    apply_timestamps("participants", record, columns, issues)
    return columns, issues


# --------------------------------------------------------------------------------------
# Enrollments
# --------------------------------------------------------------------------------------
def _reference_columns(columns: dict, ctx: Context, issues: list[Issue]) -> None:
    """Does the participant and does the training of this enrollment exist?"""
    columns["has_participant_reference"] = columns["participant_id"] in ctx.participants
    columns["has_training_reference"] = columns["training_id"] in ctx.training_ids
    if not columns["has_participant_reference"]:
        issues.append(warning("missing_participant_reference"))
    if not columns["has_training_reference"]:
        issues.append(warning("missing_training_reference"))


def _enrollment_timing_warnings(
    columns: dict, planned_end: date | None, issues: list[Issue]
) -> None:
    changed, created = columns.get("status_changed_at_utc"), columns.get(
        "created_at_utc"
    )
    if changed and created and changed < created:
        issues.append(warning("status_changed_before_created"))
    end_of_reporting_day = datetime.combine(
        REFERENCE_DATE + timedelta(days=1), time.min, tzinfo=BERLIN
    )
    if changed and changed >= end_of_reporting_day:
        issues.append(warning("status_changed_after_reference_date"))
    if columns["status"] == "aktiv" and planned_end and planned_end < REFERENCE_DATE:
        issues.append(warning("active_past_planned_end"))


def enrich_enrollments(record: dict, ctx: Context) -> tuple[dict, list[Issue]]:
    issues: list[Issue] = []
    original_training_id = record.get("training_id")
    training_id = normalize_upper(original_training_id)
    columns = {
        "enrollment_id": id_str(record.get("enrollment_id")),
        "participant_id": id_str(record.get("participant_id")),
        "training_id": training_id,
        "training_id_raw": raw_text(original_training_id),
    }
    if training_id is not None and training_id != original_training_id:
        issues.append(warning("training_id_normalization_applied"))

    planned_start = _date(columns, issues, record, "cohort_start_date")
    planned_end = _date(columns, issues, record, "planned_end_date")
    columns["planned_duration_days"] = (
        (planned_end - planned_start).days if planned_start and planned_end else None
    )
    if (
        columns["planned_duration_days"] is not None
        and columns["planned_duration_days"] < 0
    ):
        issues.append(warning("planned_end_before_start"))

    status_status = _category(columns, issues, record, "status", ENROLLMENT_STATUSES)
    is_mapped = status_status == "mapped"
    columns["is_status_valid"] = None if status_status == "missing" else is_mapped
    columns["is_open_status"] = (
        (columns["status"] in OPEN_ENROLLMENT_STATUSES) if is_mapped else None
    )

    _reference_columns(columns, ctx, issues)
    apply_timestamps("enrollments", record, columns, issues)
    _enrollment_timing_warnings(columns, planned_end, issues)
    return columns, issues


# --------------------------------------------------------------------------------------
# Progress events
# --------------------------------------------------------------------------------------
def enrich_progress_events(record: dict, ctx: Context) -> tuple[dict, list[Issue]]:
    issues: list[Issue] = []
    columns = {
        "event_id": id_str(record.get("event_id")),
        "enrollment_id": id_str(record.get("enrollment_id")),
    }
    type_status = _category(columns, issues, record, "event_type", EVENT_TYPES)
    columns["is_event_type_valid"] = (
        None if type_status == "missing" else type_status == "mapped"
    )

    module_id = normalize_upper(record.get("module_id"))
    enrollment = ctx.enrollments.get(columns["enrollment_id"])
    enrollment_training = enrollment["training_id"] if enrollment else None
    module_training = ctx.module_training.get(module_id)
    columns |= {
        "module_id": module_id,
        "module_id_raw": raw_text(record.get("module_id")),
        "enrollment_training_id": enrollment_training,
        "module_training_id": module_training,
        "has_enrollment_reference": enrollment is not None,
        "has_module_reference": module_id in ctx.module_training,
        "is_module_in_enrollment_training": None,
    }
    if enrollment is None:
        issues.append(warning("missing_enrollment_reference"))
    if not columns["has_module_reference"]:
        issues.append(warning("missing_module_reference"))
    if module_training is not None and enrollment_training is not None:
        columns["is_module_in_enrollment_training"] = (
            module_training == enrollment_training
        )
        if module_training != enrollment_training:
            issues.append(warning("module_not_in_enrollment_training"))

    apply_timestamps("progress_events", record, columns, issues)
    columns["is_event_before_cohort_start"] = None
    event_at = columns.get("event_at_utc")
    cohort_start = enrollment["cohort_start"] if enrollment else None
    if event_at is not None and cohort_start is not None:
        columns["is_event_before_cohort_start"] = (
            event_at.astimezone(BERLIN).date() < cohort_start
        )
        if columns["is_event_before_cohort_start"]:
            issues.append(warning("event_before_cohort_start"))
    return columns, issues


# --------------------------------------------------------------------------------------
# Survey responses
# --------------------------------------------------------------------------------------
def _numeric_answer_columns(
    original: str,
    score: Decimal,
    context: str,
    question: str,
    allowed_range,
    issues: list[Issue],
) -> dict:
    """Score and validity of an answer that is a number (``9``) or a number over a scale (``6/10``)."""
    low, high = allowed_range
    columns = {"answer_score": None, "nps_category": None}
    if (
        abs(score) < MAX_SCORE
    ):  # an absurd value is not stored as a score; it is reported as invalid below
        columns["answer_score"] = score.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    is_valid = score == score.to_integral_value() and low <= score <= high
    if context not in (UNKNOWN, str(high)):
        is_valid = False
        issues.append(
            error(
                "survey_answer_scale_mismatch",
                "invalid_number",
                f"answer_value={original!r} uses scale /{context}, {question} is {low}-{high}",
            )
        )
    elif not is_valid:
        issues.append(
            error(
                "survey_answer_out_of_range",
                "invalid_number",
                f"answer_value={original!r} is outside {low}-{high} or not a whole number",
            )
        )
    columns["is_answer_valid"] = is_valid
    if is_valid and (context != UNKNOWN or "," in str(original)):
        issues.append(warning("survey_answer_format_normalized"))
    if is_valid and question == "nps":
        columns["nps_category"] = nps_category(score)
    return columns


def _answer_columns(record: dict, question: str, issues: list[Issue]) -> dict:
    """The answer as text, its numeric score where the question has one, and whether it is valid."""
    original = record.get("answer_value")
    score, context, kind = parse_answer(original)
    columns = {
        "answer_value_raw": raw_text(original),
        "answer_value": (
            original.strip() if isinstance(original, str) and original.strip() else None
        ),
        "is_answer_missing": is_missing(original),
        "answer_score": None,
        "answer_context": context,
        "is_answer_valid": None,
        "nps_category": None,
    }
    allowed_range = NUMERIC_SURVEY_RANGES.get(question)
    if allowed_range is None:
        # Free text (or an unknown question) is never converted to a number.
        columns["answer_context"] = UNKNOWN if kind == "missing" else "text"
    elif kind == "missing":
        issues.append(warning("missing_survey_answer"))
    elif kind == "text":
        columns["is_answer_valid"] = False
        issues.append(
            error(
                "malformed_survey_answer",
                "invalid_number",
                f"answer_value={original!r} is not numeric",
            )
        )
    else:
        columns |= _numeric_answer_columns(
            original, score, context, question, allowed_range, issues
        )
    return columns


def enrich_survey_responses(record: dict, ctx: Context) -> tuple[dict, list[Issue]]:
    issues: list[Issue] = []
    columns = {
        "response_id": id_str(record.get("response_id")),
        "enrollment_id": id_str(record.get("enrollment_id")),
    }
    columns["survey_week"], week_status = _whole_number(record.get("survey_week"))
    if week_status == "invalid":
        issues.append(
            error(
                "invalid_survey_week",
                "invalid_number",
                f"survey_week={record.get('survey_week')!r}",
            )
        )
    _category(columns, issues, record, "survey_type", SURVEY_TYPES)
    _category(columns, issues, record, "question_key", tuple(SURVEY_QUESTIONS))
    survey_type, question = columns["survey_type"], columns["question_key"]

    columns["has_enrollment_reference"] = columns["enrollment_id"] in ctx.enrollments
    if not columns["has_enrollment_reference"]:
        issues.append(warning("missing_enrollment_reference"))

    expected_survey_type = SURVEY_QUESTIONS.get(question)
    columns["is_question_valid_for_survey_type"] = (
        None if expected_survey_type is None else expected_survey_type == survey_type
    )
    if expected_survey_type is not None and expected_survey_type != survey_type:
        issues.append(
            error(
                "question_not_valid_for_survey_type",
                "invalid_category",
                f"question_key={question!r} does not belong to survey_type={survey_type!r}",
            )
        )

    week = columns["survey_week"]  # only the weekly feedback has a week
    columns["is_survey_week_valid"] = (
        (week is not None and week >= 1)
        if survey_type == "wochenfeedback"
        else week is None
    )
    if not columns["is_survey_week_valid"]:
        issues.append(warning("survey_week_inconsistent"))

    columns |= _answer_columns(record, question, issues)
    apply_timestamps("survey_responses", record, columns, issues)
    return columns, issues


ENRICHERS = {
    "trainings": enrich_trainings,
    "participants": enrich_participants,
    "enrollments": enrich_enrollments,
    "progress_events": enrich_progress_events,
    "survey_responses": enrich_survey_responses,
}
