-- Q2: Share completed / abandoned for enrollments whose planned end is before the reporting date,
-- per training and funding type. Other statuses stay in the denominator (other_status_count).
select
    training_id,
    training_name,
    funding_type,
    eligible_enrollment_count,
    completed_count,
    abandoned_count,
    other_status_count,
    round(completed_share, 4) as completed_share,
    round(abandoned_share, 4) as abandoned_share
from {{ ref('rpt_completion_abandonment_by_training_funding') }}
order by training_id, funding_type
