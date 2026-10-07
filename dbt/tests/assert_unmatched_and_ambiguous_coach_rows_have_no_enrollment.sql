-- Ambiguous and unmatched coach rows must never be forced into an enrollment or participant.
select *
from {{ ref('fct_coach_assignments') }}
where match_status in ('ambiguous', 'unmatched')
  and (matched_enrollment_id is not null or is_confident_assignment)
