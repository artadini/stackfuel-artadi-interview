-- completed + abandoned + other = eligible; shares in 0..1 and adding up to 1.
select *
from {{ ref('rpt_completion_abandonment_by_training_funding') }}
where completed_count + abandoned_count + other_status_count <> eligible_enrollment_count
   or completed_share not between 0 and 1
   or abandoned_share not between 0 and 1
   or abs(completed_share + abandoned_share + other_status_share - 1) > 1e-9
