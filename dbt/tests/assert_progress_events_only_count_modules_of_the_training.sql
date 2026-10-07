-- completed modules can never exceed the modules of the training.
select *
from {{ ref('int_enrollment_progress') }}
where completed_module_count > total_module_count
