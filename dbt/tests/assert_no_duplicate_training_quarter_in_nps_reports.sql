-- NPS models: no duplicate (training_id, feedback_quarter).
select 'rpt_nps_by_training_quarter' as model_name, training_id, feedback_quarter, count(*) as row_count
from {{ ref('rpt_nps_by_training_quarter') }} group by training_id, feedback_quarter having count(*) > 1
union all
select 'int_feedback_nps', training_id, feedback_quarter, count(*)
from {{ ref('int_feedback_nps') }} group by training_id, feedback_quarter having count(*) > 1
