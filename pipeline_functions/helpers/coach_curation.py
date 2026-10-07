"""Cleaning and matching of the manually maintained coach workbook (sheet ``Betreuung``).

The workbook has no key shared with the CRM, so every row is linked to a participant and an
enrollment with a conservative matching ladder (see ``match_row``). A match is never forced: a row
that cannot be linked uniquely stays ``unmatched`` or ``ambiguous`` and is marked for review.
Nothing is deduplicated; rows sharing a business key are only flagged.

Every original cell is kept in a ``*_raw`` column, and every cleaned value is derived from the
untouched cell, never from another cleaned value.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date

from .config import COACH_REQUIRED_COLUMNS
from .curation import Context, id_str, raw_text
from .quarantine import Issue, error, review, warning
from .standardize import (
    UNKNOWN,
    clean_text,
    is_missing,
    is_valid_email,
    name_match_key,
    normalize_ampel,
    normalize_email,
    parse_cohort_date,
    parse_flexible_date,
    split_participant_name,
    trim_text,
)
from .training_mapping import map_training

from ..helpers.extraction_types import (
    Match,
)

MISSING_KEY_PART = "<unknown>"


def coach_assignment_id(
    source_file_name: str, snapshot_date: date | str, excel_row_number: int
) -> str:
    """A stable ID for one workbook row: SHA-256 of ``file|snapshot_date|excel_row``.

    The same file, snapshot date and row always give the same ID (no random UUID).
    """
    day = (
        snapshot_date.isoformat()
        if isinstance(snapshot_date, date)
        else str(snapshot_date)
    )
    return hashlib.sha256(
        f"{source_file_name}|{day}|{excel_row_number}".encode("utf-8")
    ).hexdigest()


def business_key(
    email: str | None, training_id: str | None, cohort_start: date | None
) -> tuple[str, bool]:
    """``(key, is_complete)`` from the normalized e-mail, training and cohort start.

    A missing part is written as ``<unknown>``. The key is not unique (an e-mail can be missing or
    reused); it only serves to find rows that describe the same participation.
    """
    parts = [
        email or MISSING_KEY_PART,
        training_id or MISSING_KEY_PART,
        cohort_start.isoformat() if cohort_start else MISSING_KEY_PART,
    ]
    return "|".join(parts), MISSING_KEY_PART not in parts


# --------------------------------------------------------------------------------------
# Matching a row to the CRM
# --------------------------------------------------------------------------------------
def enrollments_of(
    ctx: Context, participant_ids, training_id: str, cohort_start: date | None
) -> list[str]:
    """Enrollments of these participants in this training (and cohort, if one is given)."""
    found = []
    for participant_id in sorted(participant_ids):
        for enrollment_id in ctx.participant_enrollments.get(participant_id, []):
            enrollment = ctx.enrollments[enrollment_id]
            if enrollment["training_id"] != training_id:
                continue
            if cohort_start is not None and enrollment["cohort_start"] != cohort_start:
                continue
            found.append(enrollment_id)
    return found


def match_enrollment_of_known_participant(
    ctx: Context,
    participant_id: str,
    training_id: str | None,
    cohort_start: date | None,
) -> tuple[str | None, str, int]:
    """Find the enrollment of a participant we already identified: ``(enrollment id, status, candidates)``."""
    if training_id is None:
        return (
            None,
            "unmatched",
            len(ctx.participant_enrollments.get(participant_id, [])),
        )
    candidates = enrollments_of(ctx, [participant_id], training_id, cohort_start)
    if not candidates and cohort_start is not None:
        candidates = enrollments_of(
            ctx, [participant_id], training_id, None
        )  # the cohort may be mistyped
    if len(candidates) == 1:
        return candidates[0], "matched", 1
    return None, ("ambiguous" if candidates else "unmatched"), len(candidates)


def match_from_enrollments(ctx: Context, candidates: list[str], method: str) -> Match:
    if len(candidates) == 1:
        enrollment_id = candidates[0]
        participant_id = ctx.enrollments[enrollment_id]["participant_id"]
        return Match("matched", method, participant_id, enrollment_id, 1, 1, "matched")
    return Match(
        "ambiguous",
        "ambiguous_candidates",
        None,
        None,
        len(candidates),
        len(candidates),
        "ambiguous",
    )


def match_row(
    ctx: Context,
    email: str | None,
    name_key: str | None,
    training_id: str | None,
    cohort_start: date | None,
) -> Match:
    """Link a workbook row to the CRM. In this order:

    1. the normalized e-mail belongs to exactly one participant (``unique_email``);
    2. name + training + cohort start select exactly one enrollment (``name_training_cohort``);
    3. name + training select exactly one enrollment (``name_training``).

    A name is never used alone. If an e-mail is shared by several participants, only the name steps
    can tell them apart. Several candidates left over give ``ambiguous``, never a guess.
    """
    email_ids = set(ctx.email_to_ids.get(email, ())) if email else set()
    if len(email_ids) == 1:
        participant_id = next(iter(email_ids))
        enrollment_id, enrollment_status, enrollment_candidates = (
            match_enrollment_of_known_participant(
                ctx, participant_id, training_id, cohort_start
            )
        )
        return Match(
            "matched",
            "unique_email",
            participant_id,
            enrollment_id,
            1,
            enrollment_candidates,
            enrollment_status,
        )

    name_ids = set(ctx.name_to_ids.get(name_key, ())) if name_key else set()
    candidates_by_name = (email_ids & name_ids) or name_ids
    if candidates_by_name and training_id is not None:
        if cohort_start is not None:
            with_cohort = enrollments_of(
                ctx, candidates_by_name, training_id, cohort_start
            )
            if with_cohort:
                return match_from_enrollments(ctx, with_cohort, "name_training_cohort")
        without_cohort = enrollments_of(ctx, candidates_by_name, training_id, None)
        if without_cohort:
            return match_from_enrollments(ctx, without_cohort, "name_training")

    competing_participants = max(len(email_ids), len(candidates_by_name))
    if competing_participants > 1:
        return Match(
            "ambiguous",
            "ambiguous_candidates",
            None,
            None,
            competing_participants,
            0,
            "unmatched",
        )
    return Match("unmatched", "no_match")


def match_issues(match: Match) -> list[Issue]:
    """What a reviewer should know about the match (nothing for a clean match)."""
    if match.status == "unmatched":
        return [
            review(
                "unmatched_participant",
                "unmatched_participant",
                "no participant could be linked (no unique e-mail, no unique name+training match)",
            )
        ]
    if match.status == "ambiguous":
        return [
            review(
                "ambiguous_participant_match",
                "ambiguous_participant",
                f"{match.candidate_count} candidates remain; not matched automatically",
            )
        ]
    if match.enrollment_status == "ambiguous":
        return [
            review(
                "ambiguous_enrollment_match",
                "ambiguous_enrollment",
                f"participant {match.participant_id} has {match.enrollment_candidate_count} matching enrollments",
            )
        ]
    if match.enrollment_status == "unmatched":
        return [warning("no_matching_enrollment")]
    return []


# --------------------------------------------------------------------------------------
# Cleaning one row
# --------------------------------------------------------------------------------------
def participant_columns(record: dict, ctx: Context, issues: list[Issue]) -> dict:
    """Name and e-mail of the participant."""
    name = record.get("Teilnehmer*in")
    last_name, first_name = split_participant_name(name)
    email = record.get("E-Mail")
    email_key = normalize_email(email)

    if name_match_key(name) is None:
        issues.append(warning("missing_teilnehmer"))
    if email_key is None:
        issues.append(warning("missing_email"))
    elif is_valid_email(email_key) is False:
        issues.append(warning("invalid_email_format"))
    return {
        "teilnehmer_raw": raw_text(name),
        "participant_last_name_raw": last_name,
        "participant_first_name_raw": first_name,
        "name_match_key": name_match_key(name),
        "email_raw": raw_text(email),
        "email": trim_text(email),
        "email_match_key": email_key,
        "email_assignment_count": ctx.coach_email_counts.get(email_key, 0),
    }


def training_columns(record: dict, issues: list[Issue]) -> dict:
    """The training of the row, mapped to a catalog ID where its spelling is known."""
    training = record.get("Training")
    mapped = map_training(training)
    if mapped.status == "missing":
        issues.append(
            review("missing_training", "unmatched_training", "Training is empty")
        )
    elif mapped.status == "unmatched":
        issues.append(
            review(
                "unmatched_training",
                "unmatched_training",
                f"Training {training!r} is not in the training mapping table",
            )
        )
    elif mapped.status == "ambiguous":
        issues.append(
            review(
                "ambiguous_training",
                "ambiguous_training",
                f"Training {training!r} fits {list(mapped.candidate_ids)}; track or variant is unclear",
            )
        )
    return {
        "training_raw": raw_text(training),
        "training_id": mapped.training_id,
        "training_match_status": (
            "unmatched" if mapped.status == "missing" else mapped.status
        ),
    }


def cohort_columns(record: dict, issues: list[Issue]) -> dict:
    original = record.get("Kohorte")
    cohort_start, status = parse_cohort_date(original)
    if status == "invalid":
        issues.append(
            error(
                "invalid_cohort_date",
                "invalid_date",
                f"Kohorte={original!r} is not a date",
            )
        )
    elif status == "missing":
        issues.append(warning("missing_cohort"))
    return {
        "kohorte_raw": raw_text(original),
        "cohort_start_date": cohort_start,
        "cohort_date_parse_status": status,
    }


def coach_and_traffic_light_columns(record: dict, issues: list[Issue]) -> dict:
    coach = record.get("Coach")
    cleaned_coach = clean_text(coach) or UNKNOWN
    if cleaned_coach == UNKNOWN:
        issues.append(warning("missing_coach"))

    ampel = record.get("Ampel")
    ampel_value, ampel_status = normalize_ampel(ampel)
    if ampel_status == "unmapped":
        issues.append(
            error(
                "invalid_ampel",
                "invalid_category",
                f"Ampel={ampel!r} is not a known colour",
            )
        )
    elif ampel_status == "missing":
        issues.append(warning("missing_ampel"))
    return {
        "coach_raw": raw_text(coach),
        "coach": cleaned_coach,
        "ampel_raw": raw_text(ampel),
        "ampel": ampel_value,
        "ampel_status": ampel_status,
    }


def contact_and_notes_columns(record: dict, issues: list[Issue]) -> dict:
    contact = record.get("Letzter Kontakt")
    contact_date, contact_status = parse_flexible_date(contact)
    if contact_status == "invalid":
        issues.append(
            error(
                "invalid_last_contact_date",
                "invalid_date",
                f"Letzter Kontakt={contact!r} is not a date",
            )
        )
    notes = record.get("Notizen")
    return {
        "letzter_kontakt_raw": raw_text(contact),
        "last_contact_date": contact_date,
        "last_contact_date_parse_status": contact_status,
        "notizen_raw": raw_text(notes),
        "notizen": trim_text(
            notes
        ),  # free text: only trimmed; an empty note stays NULL
    }


def completeness_columns(record: dict, issues: list[Issue]) -> dict:
    empty_required = [
        name for name in COACH_REQUIRED_COLUMNS if is_missing(record.get(name))
    ]
    if empty_required:
        issues.append(
            warning("incomplete_row", f"empty required columns: {empty_required}")
        )
    return {
        "is_incomplete": bool(empty_required),
        "completeness_status": "incomplete" if empty_required else "complete",
    }


def clean_row(record: dict, row_number: int, ctx: Context) -> tuple[dict, list[Issue]]:
    """All columns of one workbook row except the keys and the match, which need the other rows."""
    issues: list[Issue] = []
    columns = {"excel_row_number": row_number}
    columns |= participant_columns(record, ctx, issues)
    columns |= training_columns(record, issues)
    columns |= cohort_columns(record, issues)
    columns |= coach_and_traffic_light_columns(record, issues)
    columns |= contact_and_notes_columns(record, issues)
    columns |= completeness_columns(record, issues)
    return columns, issues


# --------------------------------------------------------------------------------------
# The whole workbook
# --------------------------------------------------------------------------------------
def curate_coach_rows(
    records: list[dict],
    row_numbers: list[int],
    ctx: Context,
    *,
    source_file_name: str,
    snapshot_date: date,
) -> list[tuple[dict, list[Issue]]]:
    """Clean every workbook row and match it to the CRM. Returns ``(columns, issues)`` per row, in input order."""
    # First pass: clean each row and give it its keys.
    rows = []
    for record, row_number in zip(records, row_numbers):
        columns, issues = clean_row(record, row_number, ctx)
        columns["coach_assignment_id"] = coach_assignment_id(
            source_file_name, snapshot_date, row_number
        )
        key, is_complete = business_key(
            columns["email_match_key"],
            columns["training_id"],
            columns["cohort_start_date"],
        )
        columns["coach_assignment_business_key"] = key
        columns["has_complete_business_key"] = is_complete
        rows.append((columns, issues))

    # Second pass: needs to know how many rows share a business key.
    rows_per_key = Counter(
        columns["coach_assignment_business_key"] for columns, _ in rows
    )
    for columns, issues in rows:
        key = columns["coach_assignment_business_key"]
        shares_key = rows_per_key[key] > 1
        columns["is_business_key_duplicate"] = shares_key
        if shares_key:
            issues.append(
                warning(
                    "duplicate_business_key", f"{rows_per_key[key]} rows share {key!r}"
                )
            )

        match = match_row(
            ctx,
            columns["email_match_key"],
            columns["name_match_key"],
            columns["training_id"],
            columns["cohort_start_date"],
        )
        if (
            match.status == "matched"
            and shares_key
            and columns["has_complete_business_key"]
        ):
            # Several rows describe the same participant, training and cohort: do not force a match.
            match = Match(
                "ambiguous",
                "ambiguous_candidates",
                None,
                None,
                rows_per_key[key],
                0,
                "unmatched",
            )

        columns |= {
            "matched_participant_id": id_str(match.participant_id),
            "matched_enrollment_id": id_str(match.enrollment_id),
            "match_status": match.status,
            "match_method": match.method,
            "candidate_count": match.candidate_count,
            "enrollment_candidate_count": match.enrollment_candidate_count,
            "enrollment_match_status": match.enrollment_status,
        }
        issues += match_issues(match)
    return rows
