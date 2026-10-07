-- Q1: How many enrollments are active at the reporting date, per training?
-- Active = status 'aktiv' (current CRM status; no status history exists). Counts enrollments.
select
    training_id,
    training_name,
    active_enrollment_count,
    reporting_date,
    data_quality_flags
from "stackfuel"."reporting"."rpt_active_enrollments_by_training"
order by training_id