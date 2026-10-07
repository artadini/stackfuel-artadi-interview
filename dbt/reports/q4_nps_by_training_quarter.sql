-- Q4: Net Promoter Score per training and quarter from completion feedback
-- (promoters 9-10 minus detractors 0-6, in percent of valid answers).
select
    training_id,
    training_name,
    feedback_quarter_label,
    valid_response_count,
    promoter_count,
    passive_count,
    detractor_count,
    round(nps, 1) as nps
from "stackfuel"."reporting"."rpt_nps_by_training_quarter"
order by training_id, feedback_quarter