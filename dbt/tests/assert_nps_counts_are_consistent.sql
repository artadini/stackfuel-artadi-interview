-- promoters + passives + detractors = valid responses, and the NPS equals its definition.
select *
from {{ ref('rpt_nps_by_training_quarter') }}
where promoter_count + passive_count + detractor_count <> valid_response_count
   or nps is null
   or nps not between -100 and 100
   or abs(nps - 100.0 * (promoter_count - detractor_count) / valid_response_count) > 1e-9
