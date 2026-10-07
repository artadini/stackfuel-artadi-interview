{#- One row per workbook row and snapshot day, all snapshot days kept. Rows are not filtered by
    validation status: a row with an unmappable Ampel still describes a valid assignment (its
    ampel is `unknown`). Ambiguous and unmatched rows stay and carry no enrollment. -#}

select
    matches.coach_assignment_id,
    matches.snapshot_date,
    matches.coach,
    matches.training_id,
    stg.training_unmapped_raw,
    matches.cohort_start_date,
    matches.matched_participant_id,
    matches.matched_enrollment_id,
    matches.match_status,
    matches.match_method,
    matches.is_confident_assignment,
    matches.enrollment_status,
    matches.is_active_or_paused,
    matches.ampel,
    matches.is_red,
    matches.last_contact_date,
    matches.business_key_duplicate_flag,
    matches.has_conflicting_coach,
    matches.is_latest_snapshot,
    matches.coach_snapshot_as_of_date,
    matches.snapshot_mismatch_flag,
    matches.validation_status,
    matches.quality_flags,
    matches.ingested_at
from {{ ref('int_coach_assignment_matches') }} as matches
inner join {{ ref('stg_coach__assignments') }} as stg
    on stg.coach_assignment_id = matches.coach_assignment_id
    and stg.snapshot_date = matches.snapshot_date
