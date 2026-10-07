{#- Question 4: NPS per training and quarter from completion feedback (see int_feedback_nps for the
    population). NPS = 100 * (promoters - detractors) / valid responses. -#}

select
    nps.training_id,
    trainings.training_name,
    nps.feedback_quarter,
    nps.feedback_quarter_label,
    nps.valid_response_count,
    nps.promoter_count,
    nps.passive_count,
    nps.detractor_count,
    nps.nps,
    {{ reporting_date() }} as reporting_date
from {{ ref('int_feedback_nps') }} as nps
inner join {{ ref('dim_trainings') }} as trainings
    on trainings.training_id = nps.training_id
