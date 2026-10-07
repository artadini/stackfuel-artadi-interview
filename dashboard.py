"""Training Operations dashboard: the five questions of section 3.4 on the dbt reporting models.

Run:   make dashboard            (or: .venv-dashboard/bin/streamlit run dashboard.py)

Data:  read-only from db/duckdb/stackfuel.duckdb, schema `reporting` (built by `make dbt-build`).
       Only `marts.dim_trainings` is read in addition, for the variant filter (the reporting models
       carry no variant). The connection is opened per load and closed again, so the pipeline and
       dbt can write to the file while the dashboard is open (reload the page afterwards).
"""

import os
from datetime import date
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

DB_PATH = Path(
    os.environ.get(
        "STACKFUEL_DUCKDB_PATH",
        Path(__file__).resolve().parent / "db" / "duckdb" / "stackfuel.duckdb",
    )
)
REPORTING_MODELS = [
    "rpt_active_enrollments_by_training",
    "rpt_completion_abandonment_by_training_funding",
    "rpt_progress_vs_time",
    "rpt_progress_vs_time_summary",
    "rpt_nps_by_training_quarter",
    "rpt_coach_utilization",
    "rpt_coach_unassigned_enrollments",
]
RED, AMBER, BLUE, GREY, GREEN = "#d62728", "#ff9f1c", "#1f77b4", "#9aa0a6", "#2ca02c"
FUNDING_COLORS = {
    "bildungsgutschein": "#1f77b4",
    "firmenkunde": "#2ca02c",
    "selbstzahler": "#ff7f0e",
    "unknown": "#9aa0a6",
}
# Presentation-only thresholds for the "needs attention" box (not part of any KPI definition).
MIN_ACTIVE_FOR_RANKING = 5
MIN_ELIGIBLE_FOR_RANKING = 20
MIN_NPS_RESPONSES_FOR_RANKING = 20
SMALL_GROUP = 10

st.set_page_config(
    page_title="Training Operations KPIs", page_icon=":bar_chart:", layout="wide"
)


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner="Loading reporting models ...")
def load_data(path: str, modified: float) -> dict[str, pd.DataFrame]:
    """Read all reporting models once. `modified` (file mtime) refreshes the cache after a dbt build."""
    connection = duckdb.connect(path, read_only=True)
    try:
        connection.execute("SET TimeZone = 'UTC'")
        frames = {
            name: connection.execute(f"select * from reporting.{name}").df()
            for name in REPORTING_MODELS
        }
        frames["dim_trainings"] = connection.execute(
            "select training_id, training_name, track, variant from marts.dim_trainings"
        ).df()
    finally:
        connection.close()
    return frames


def pct(value, digits: int = 0) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value * 100:.{digits}f}%"


def nps_text(value) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value:+.0f}"


def nps_from_counts(promoters, detractors, responses):
    return None if responses == 0 else 100.0 * (promoters - detractors) / responses


def label(training_id: str, name: str) -> str:
    return f"{training_id} · {name}"


if not DB_PATH.exists():
    st.error(
        f"Database not found: {DB_PATH}. Run `python pipeline.py` and `make dbt-build` first."
    )
    st.stop()
try:
    data = load_data(str(DB_PATH), DB_PATH.stat().st_mtime)
except duckdb.Error as error:
    st.error(
        "Could not read the `reporting` models. Run `make dbt-build` first, and make sure no other "
        f"process holds a write lock on the database.\n\n`{error}`"
    )
    st.stop()

active = data["rpt_active_enrollments_by_training"]
completion = data["rpt_completion_abandonment_by_training_funding"]
progress = data["rpt_progress_vs_time"]
summary = data["rpt_progress_vs_time_summary"]
nps = data["rpt_nps_by_training_quarter"].copy()
nps["feedback_quarter"] = pd.to_datetime(nps["feedback_quarter"])
coaches = data["rpt_coach_utilization"]
unassigned = data["rpt_coach_unassigned_enrollments"].iloc[0]
dim = data["dim_trainings"]
reporting_date = "31.08.2026"
names = dict(zip(dim["training_id"], dim["training_name"]))

# --------------------------------------------------------------------------------------
# Sidebar filters
# --------------------------------------------------------------------------------------
st.sidebar.header("Filters")
variants = sorted(dim["variant"].dropna().unique())
chosen_variants = st.sidebar.multiselect(
    "Variant", variants, default=variants, help="vollzeit / teilzeit / berufsbegleitend"
)
candidates = dim[dim["variant"].isin(chosen_variants)].sort_values("training_id")
chosen_labels = st.sidebar.multiselect(
    "Training",
    [label(r.training_id, r.training_name) for r in candidates.itertuples()],
    default=[label(r.training_id, r.training_name) for r in candidates.itertuples()],
)
selected = {text.split(" · ")[0] for text in chosen_labels}
funding_types = sorted(completion["funding_type"].unique())
chosen_funding = st.sidebar.multiselect(
    "Funding type (question 2)", funding_types, default=funding_types
)
quarters = sorted(nps["feedback_quarter"].unique())
quarter_labels = [
    f"{q.year}-Q{(q.month - 1) // 3 + 1}" for q in map(pd.Timestamp, quarters)
]
first_quarter, last_quarter = st.sidebar.select_slider(
    "NPS quarters",
    options=quarter_labels,
    value=(quarter_labels[0], quarter_labels[-1]),
)
st.sidebar.caption(
    f"Reporting date (Stichtag): **{reporting_date}**. Filters apply to questions 1 to 4; "
    "the coach list (question 5) has no training dimension."
)

if not selected:
    st.warning("Select at least one training in the sidebar.")
    st.stop()

quarter_range = quarter_labels[
    quarter_labels.index(first_quarter) : quarter_labels.index(last_quarter) + 1
]
nps["quarter_label"] = nps["feedback_quarter_label"]
nps_f = nps[
    nps["training_id"].isin(selected) & nps["quarter_label"].isin(quarter_range)
].copy()
active_f = active[active["training_id"].isin(selected)]
completion_f = completion[
    completion["training_id"].isin(selected)
    & completion["funding_type"].isin(chosen_funding)
]
progress_f = progress[progress["training_id"].isin(selected)].copy()
summary_f = summary[summary["training_id"].isin(selected) & ~summary["is_total"]]

# --------------------------------------------------------------------------------------
# Header and KPI tiles
# --------------------------------------------------------------------------------------
st.title("Training Operations: KPI dashboard")
st.caption(
    f"Status at {reporting_date} · source: dbt `reporting` models in `{DB_PATH.name}` · "
    f"{len(selected)} of {len(dim)} trainings selected"
)

active_total = int(active_f["active_enrollment_count"].sum())
evaluable = int(summary_f["evaluable_active_enrollment_count"].sum())
behind_total = int(summary_f["behind_schedule_count"].sum())
eligible_total = int(completion_f["eligible_enrollment_count"].sum())
completed_total = int(completion_f["completed_count"].sum())
abandoned_total = int(completion_f["abandoned_count"].sum())
promoters, detractors, responses = (
    int(nps_f[c].sum())
    for c in ("promoter_count", "detractor_count", "valid_response_count")
)

tiles = st.columns(6)
tiles[0].metric("Active enrollments", f"{active_total}", help="Status 'aktiv' (Q1)")
tiles[1].metric(
    "Behind schedule",
    pct(behind_total / evaluable if evaluable else None),
    help=f"{behind_total} of {evaluable} active enrollments are >= 15 percentage points behind the plan (Q3)",
)
tiles[2].metric(
    "Completed",
    pct(completed_total / eligible_total if eligible_total else None),
    help=f"{completed_total} of {eligible_total} enrollments with planned end before the cutoff (Q2)",
)
tiles[3].metric(
    "Abandoned",
    pct(abandoned_total / eligible_total if eligible_total else None),
    help=f"{abandoned_total} of {eligible_total} enrollments with planned end before the cutoff (Q2)",
)
tiles[4].metric(
    "NPS (selection)",
    nps_text(nps_from_counts(promoters, detractors, responses)),
    help=f"{responses} valid completion answers in the selected quarters (Q4)",
)
tiles[5].metric(
    "Without coach",
    f"{int(unassigned['unassigned_active_or_paused_enrollment_count'])}",
    help=f"of {int(unassigned['active_or_paused_enrollment_count'])} active or paused enrollments, all trainings (Q5)",
)

# --------------------------------------------------------------------------------------
# Needs attention
# --------------------------------------------------------------------------------------
st.subheader("Where action is needed")
attention = st.columns(3)

worst_behind = (
    summary_f[summary_f["active_enrollment_count"] >= MIN_ACTIVE_FOR_RANKING]
    .sort_values("behind_schedule_share", ascending=False)
    .head(2)
)
with attention[0]:
    st.markdown("**Learners behind schedule**")
    for row in worst_behind.itertuples():
        st.warning(
            f"{label(row.training_id, row.training_name)}: **{pct(row.behind_schedule_share)}** of "
            f"{row.active_enrollment_count} active enrollments are at least 15 points behind"
        )
    overdue = int(active_f["past_planned_end_count"].sum())
    if overdue:
        st.error(
            f"**{overdue}** enrollments are still 'aktiv' although their planned end is before the cutoff"
        )

by_training_completion = completion_f.groupby("training_id", as_index=False)[
    ["eligible_enrollment_count", "completed_count", "abandoned_count"]
].sum()
by_training_completion["abandoned_share"] = (
    by_training_completion["abandoned_count"]
    / by_training_completion["eligible_enrollment_count"]
)
worst_abandon = by_training_completion[
    by_training_completion["eligible_enrollment_count"] >= MIN_ELIGIBLE_FOR_RANKING
]
worst_abandon = worst_abandon.sort_values("abandoned_share", ascending=False).head(2)
with attention[1]:
    st.markdown("**Abandonment and satisfaction**")
    for row in worst_abandon.itertuples():
        st.warning(
            f"{label(row.training_id, names.get(row.training_id, ''))}: **{pct(row.abandoned_share)}** abandoned "
            f"({row.abandoned_count} of {row.eligible_enrollment_count} finished-by-plan enrollments)"
        )
    nps_by_training = nps_f.groupby("training_id", as_index=False)[
        ["promoter_count", "detractor_count", "valid_response_count"]
    ].sum()
    nps_by_training["nps"] = (
        100.0
        * (nps_by_training["promoter_count"] - nps_by_training["detractor_count"])
        / nps_by_training["valid_response_count"]
    )
    low_nps = (
        nps_by_training[
            nps_by_training["valid_response_count"] >= MIN_NPS_RESPONSES_FOR_RANKING
        ]
        .sort_values("nps")
        .head(2)
    )
    for row in low_nps.itertuples():
        text = (
            f"{label(row.training_id, names.get(row.training_id, ''))}: NPS **{nps_text(row.nps)}** "
            f"({row.valid_response_count} answers)"
        )
        (st.error if row.nps < 0 else st.warning)(text)

with attention[2]:
    st.markdown("**Coaching coverage**")
    missing = int(unassigned["unassigned_active_or_paused_enrollment_count"])
    total_open = int(unassigned["active_or_paused_enrollment_count"])
    st.error(
        f"**{missing}** of {total_open} active or paused enrollments have no confident coach assignment"
    )
    if len(coaches):
        coaches_ranked = coaches.assign(
            red_share=coaches["red_enrollment_count"]
            / coaches["active_or_paused_enrollment_count"].where(
                coaches["active_or_paused_enrollment_count"] > 0
            )
        )
        top = coaches_ranked.sort_values("red_share", ascending=False).iloc[0]
        st.warning(
            f"Highest red share: **{top['coach']}** with {int(top['red_enrollment_count'])} of "
            f"{int(top['active_or_paused_enrollment_count'])} enrollments on red"
        )
    ambiguous = int(unassigned["ambiguous_workbook_row_count"])
    if ambiguous:
        st.info(
            f"{ambiguous} workbook rows are ambiguous (not matched automatically); clarify them in the Betreuungsliste"
        )

if bool(unassigned["snapshot_mismatch_flag"]):
    st.info(
        f"Coach list dated {pd.to_datetime(unassigned['coach_snapshot_as_of_date']):%d.%m.%Y}, KPI cutoff "
        f"{reporting_date}: the one-day mismatch is a documented limitation."
    )

# --------------------------------------------------------------------------------------
# Question tabs
# --------------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "1 · Active enrollments",
        "2 · Completion & abandonment",
        "3 · Progress vs. time",
        "4 · NPS",
        "5 · Coach utilization",
        "Definitions",
    ]
)

# ---- Q1 -------------------------------------------------------------------------------
with tab1:
    st.markdown(
        "**How many enrollments are active at the reporting date, per training?**"
    )
    q1 = active_f.assign(label=lambda d: d["training_id"] + " · " + d["training_name"])
    chart = (
        alt.Chart(q1)
        .mark_bar(color=BLUE)
        .encode(
            y=alt.Y("label:N", sort="-x", title=None),
            x=alt.X("active_enrollment_count:Q", title="Active enrollments"),
            tooltip=[
                "training_name",
                "active_enrollment_count",
                "past_planned_end_count",
            ],
        )
    )
    text = chart.mark_text(align="left", dx=3).encode(text="active_enrollment_count:Q")
    st.altair_chart(
        (chart + text).properties(height=max(120, 34 * len(q1))), width="stretch"
    )
    st.dataframe(
        q1[
            [
                "training_id",
                "training_name",
                "active_enrollment_count",
                "past_planned_end_count",
            ]
        ].rename(columns={"past_planned_end_count": "of which past planned end"}),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Counts enrollments (status `aktiv`), not distinct participants. CRM keeps only the current status, "
        "so the status at the cutoff is approximated by the current one."
    )

# ---- Q2 -------------------------------------------------------------------------------
with tab2:
    st.markdown(
        "**Share completed / abandoned for enrollments whose planned end is before the cutoff, per training and funding type**"
    )
    if completion_f.empty:
        st.info("No enrollments for this selection.")
    else:
        q2 = completion_f.assign(
            training=lambda d: d["training_id"],
            small_group=lambda d: d["eligible_enrollment_count"] < SMALL_GROUP,
        )
        left, right = st.columns(2)
        for column, title, color_note in (
            (left, "Abandoned share", "abandoned_share"),
            (right, "Completed share", "completed_share"),
        ):
            with column:
                st.markdown(f"**{title}**")
                bars = (
                    alt.Chart(q2)
                    .mark_bar()
                    .encode(
                        x=alt.X("training:N", title=None),
                        xOffset="funding_type:N",
                        y=alt.Y(
                            f"{color_note}:Q",
                            axis=alt.Axis(format="%"),
                            scale=alt.Scale(domain=[0, 1]),
                            title=None,
                        ),
                        color=alt.Color(
                            "funding_type:N",
                            scale=alt.Scale(
                                domain=list(FUNDING_COLORS),
                                range=list(FUNDING_COLORS.values()),
                            ),
                            title="Funding",
                        ),
                        opacity=alt.condition(
                            "datum.small_group", alt.value(0.45), alt.value(1.0)
                        ),
                        tooltip=[
                            "training_name",
                            "funding_type",
                            "eligible_enrollment_count",
                            "completed_count",
                            "abandoned_count",
                            "other_status_count",
                            alt.Tooltip("completed_share:Q", format=".1%"),
                            alt.Tooltip("abandoned_share:Q", format=".1%"),
                        ],
                    )
                    .properties(height=300)
                )
                st.altair_chart(bars, width="stretch")
        by_funding = q2.groupby("funding_type", as_index=False)[
            [
                "eligible_enrollment_count",
                "completed_count",
                "abandoned_count",
                "other_status_count",
            ]
        ].sum()
        by_funding["completed_share"] = (
            by_funding["completed_count"] / by_funding["eligible_enrollment_count"]
        )
        by_funding["abandoned_share"] = (
            by_funding["abandoned_count"] / by_funding["eligible_enrollment_count"]
        )
        st.markdown("**By funding type (selected trainings)**")
        st.dataframe(
            by_funding,
            hide_index=True,
            width="stretch",
            column_config={
                "completed_share": st.column_config.NumberColumn(
                    "completed share", format="percent"
                ),
                "abandoned_share": st.column_config.NumberColumn(
                    "abandoned share", format="percent"
                ),
            },
        )
        st.markdown("**Detail by training and funding type**")
        st.dataframe(
            q2[
                [
                    "training_id",
                    "training_name",
                    "funding_type",
                    "eligible_enrollment_count",
                    "completed_count",
                    "abandoned_count",
                    "other_status_count",
                    "completed_share",
                    "abandoned_share",
                    "small_group",
                ]
            ].rename(columns={"small_group": f"n < {SMALL_GROUP}"}),
            hide_index=True,
            width="stretch",
            column_config={
                "completed_share": st.column_config.NumberColumn(
                    "completed share", format="percent"
                ),
                "abandoned_share": st.column_config.NumberColumn(
                    "abandoned share", format="percent"
                ),
            },
        )
        st.caption(
            f"Denominator = all enrollments with planned end before the cutoff; statuses other than abgeschlossen/abgebrochen "
            f"(for example still aktiv) are shown as 'other' and stay in the denominator. Faded bars: fewer than {SMALL_GROUP} "
            "enrollments, so the share is volatile. 'unknown' funding = enrollments without a participant record."
        )

# ---- Q3 -------------------------------------------------------------------------------
with tab3:
    st.markdown(
        "**Curriculum progress versus elapsed planned time for active enrollments; share at least 15 percentage points behind**"
    )
    if progress_f.empty:
        st.info("No active enrollments for this selection.")
    else:
        progress_f["status"] = (
            progress_f["is_behind_schedule"]
            .map({True: "Behind (gap <= -15 pts)", False: "On track or ahead"})
            .fillna("Not computable")
        )
        progress_f["training"] = (
            progress_f["training_id"] + " · " + progress_f["training_name"]
        )
        domain = ["Behind (gap <= -15 pts)", "On track or ahead", "Not computable"]
        points = (
            alt.Chart(progress_f)
            .mark_circle(size=70, opacity=0.6)
            .encode(
                x=alt.X(
                    "time_progress_share:Q",
                    title="Elapsed share of planned duration",
                    axis=alt.Axis(format="%"),
                    scale=alt.Scale(domain=[0, 1]),
                ),
                y=alt.Y(
                    "completed_module_share:Q",
                    title="Completed share of curriculum modules",
                    axis=alt.Axis(format="%"),
                    scale=alt.Scale(domain=[0, 1]),
                ),
                color=alt.Color(
                    "status:N",
                    scale=alt.Scale(domain=domain, range=[RED, BLUE, GREY]),
                    title=None,
                ),
                tooltip=[
                    "enrollment_id",
                    "training",
                    alt.Tooltip("completed_module_share:Q", format=".0%"),
                    alt.Tooltip("time_progress_share:Q", format=".0%"),
                    alt.Tooltip("progress_gap:Q", format="+.0%"),
                    "completed_module_count",
                    "total_module_count",
                    "time_progress_quality_flag",
                ],
            )
        )
        guide = pd.DataFrame(
            {"x": [0.0, 1.0], "plan": [0.0, 1.0], "threshold": [-0.15, 0.85]}
        )
        plan_line = (
            alt.Chart(guide)
            .mark_line(color="#444", strokeDash=[1, 0])
            .encode(x="x:Q", y="plan:Q")
        )
        threshold_line = (
            alt.Chart(guide)
            .mark_line(color=RED, strokeDash=[6, 4])
            .encode(x="x:Q", y=alt.Y("threshold:Q", scale=alt.Scale(domain=[0, 1])))
        )
        st.altair_chart(
            (plan_line + threshold_line + points).properties(height=420),
            width="stretch",
        )
        st.caption(
            "Each dot is one active enrollment. Solid line: progress exactly on plan. Dashed red line: 15 points behind plan; "
            "dots below it are 'in Verzug'. Dots at 100% elapsed are enrollments past their planned end."
        )

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Share behind schedule by training**")
            behind = summary_f.assign(
                label=lambda d: d["training_id"] + " · " + d["training_name"]
            )
            overall_share = (behind_total / evaluable) if evaluable else 0
            bar = (
                alt.Chart(behind)
                .mark_bar(color=RED)
                .encode(
                    y=alt.Y("label:N", sort="-x", title=None),
                    x=alt.X(
                        "behind_schedule_share:Q",
                        axis=alt.Axis(format="%"),
                        title="Behind schedule",
                        scale=alt.Scale(domain=[0, 1]),
                    ),
                    tooltip=[
                        "training_name",
                        "active_enrollment_count",
                        "behind_schedule_count",
                        alt.Tooltip("behind_schedule_share:Q", format=".1%"),
                    ],
                )
            )
            rule = (
                alt.Chart(pd.DataFrame({"x": [overall_share]}))
                .mark_rule(color="#444", strokeDash=[4, 4])
                .encode(x="x:Q")
            )
            st.altair_chart(
                (bar + rule).properties(height=max(120, 34 * len(behind))),
                width="stretch",
            )
            st.caption(f"Dashed line: selection average ({pct(overall_share, 1)}).")
        with c2:
            st.markdown("**Average progress vs. average elapsed time**")
            long = summary_f.melt(
                id_vars=["training_id"],
                value_vars=[
                    "average_completed_module_share",
                    "average_time_progress_share",
                ],
                var_name="measure",
                value_name="share",
            )
            long["measure"] = long["measure"].map(
                {
                    "average_completed_module_share": "Completed curriculum",
                    "average_time_progress_share": "Elapsed planned time",
                }
            )
            grouped = (
                alt.Chart(long)
                .mark_bar()
                .encode(
                    x=alt.X("training_id:N", title=None),
                    xOffset="measure:N",
                    y=alt.Y(
                        "share:Q",
                        axis=alt.Axis(format="%"),
                        scale=alt.Scale(domain=[0, 1]),
                        title=None,
                    ),
                    color=alt.Color(
                        "measure:N", scale=alt.Scale(range=[BLUE, GREY]), title=None
                    ),
                    tooltip=[
                        "training_id",
                        "measure",
                        alt.Tooltip("share:Q", format=".1%"),
                    ],
                )
                .properties(height=max(120, 34 * len(behind)))
            )
            st.altair_chart(grouped, width="stretch")

        st.markdown("**Enrollments furthest behind schedule**")
        worst = (
            progress_f[progress_f["is_behind_schedule"] == True]
            .sort_values("progress_gap")
            .head(25)
        )  # noqa: E712
        st.dataframe(
            worst[
                [
                    "enrollment_id",
                    "training",
                    "completed_module_count",
                    "total_module_count",
                    "completed_module_share",
                    "time_progress_share",
                    "progress_gap",
                ]
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "completed_module_share": st.column_config.NumberColumn(
                    "progress", format="percent"
                ),
                "time_progress_share": st.column_config.NumberColumn(
                    "elapsed time", format="percent"
                ),
                "progress_gap": st.column_config.NumberColumn("gap", format="%.2f"),
            },
        )

# ---- Q4 -------------------------------------------------------------------------------
with tab4:
    st.markdown(
        "**Net Promoter Score (promoters 9-10 minus detractors 0-6, in percent) from completion feedback, per training and quarter**"
    )
    if nps_f.empty:
        st.info("No valid NPS answers for this selection.")
    else:
        nps_f["training"] = nps_f["training_id"] + " · " + nps_f["training_name"]
        partial_start = pd.Timestamp(reporting_date).to_period("Q").start_time
        nps_f["partial"] = nps_f["feedback_quarter"] >= partial_start
        heat = (
            alt.Chart(nps_f)
            .mark_rect()
            .encode(
                x=alt.X("quarter_label:O", title=None, sort=quarter_labels),
                y=alt.Y("training:N", title=None),
                color=alt.Color(
                    "nps:Q",
                    scale=alt.Scale(scheme="redyellowgreen", domain=[-100, 100]),
                    title="NPS",
                ),
                tooltip=[
                    "training",
                    "quarter_label",
                    alt.Tooltip("nps:Q", format="+.0f"),
                    "valid_response_count",
                    "promoter_count",
                    "passive_count",
                    "detractor_count",
                ],
            )
        )
        labels = (
            alt.Chart(nps_f)
            .mark_text(fontSize=11)
            .encode(
                x=alt.X("quarter_label:O", sort=quarter_labels),
                y="training:N",
                text=alt.Text("nps:Q", format="+.0f"),
                color=alt.value("#111"),
            )
        )
        st.altair_chart(
            (heat + labels).properties(
                height=max(150, 38 * nps_f["training_id"].nunique())
            ),
            width="stretch",
        )

        st.markdown("**Trend**")
        line = (
            alt.Chart(nps_f)
            .mark_line(point=False)
            .encode(
                x=alt.X("quarter_label:O", title=None, sort=quarter_labels),
                y=alt.Y("nps:Q", title="NPS", scale=alt.Scale(domain=[-100, 100])),
                color=alt.Color("training_id:N", title="Training"),
            )
        )
        dots = (
            alt.Chart(nps_f)
            .mark_circle()
            .encode(
                x=alt.X("quarter_label:O", sort=quarter_labels),
                y="nps:Q",
                color="training_id:N",
                size=alt.Size(
                    "valid_response_count:Q",
                    title="Answers",
                    scale=alt.Scale(range=[30, 400]),
                ),
                tooltip=[
                    "training",
                    "quarter_label",
                    alt.Tooltip("nps:Q", format="+.0f"),
                    "valid_response_count",
                ],
            )
        )
        zero = (
            alt.Chart(pd.DataFrame({"y": [0]}))
            .mark_rule(color="#444", strokeDash=[4, 4])
            .encode(y="y:Q")
        )
        st.altair_chart((zero + line + dots).properties(height=320), width="stretch")

        st.dataframe(
            nps_f.sort_values(["training_id", "feedback_quarter"])[
                [
                    "training_id",
                    "training_name",
                    "quarter_label",
                    "valid_response_count",
                    "promoter_count",
                    "passive_count",
                    "detractor_count",
                    "nps",
                    "partial",
                ]
            ].rename(
                columns={"partial": "partial quarter", "quarter_label": "quarter"}
            ),
            hide_index=True,
            width="stretch",
            column_config={"nps": st.column_config.NumberColumn("NPS", format="%+.1f")},
        )
        st.caption(
            "Only completion feedback (abschlussfeedback), valid whole scores 0-10 submitted up to the cutoff; empty or invalid answers "
            "are excluded, never counted as 0. Quarter = UTC quarter of the submission; the quarter containing the cutoff is partial. "
            "With few answers per cell (see the bubble size and the table) a single answer moves the NPS a lot."
        )

# ---- Q5 -------------------------------------------------------------------------------
with tab5:
    st.markdown(
        "**Active or paused enrollments per coach (Betreuungsliste), how many are red, and how many could not be assigned**"
    )
    metrics = st.columns(4)
    metrics[0].metric(
        "Active or paused enrollments",
        int(unassigned["active_or_paused_enrollment_count"]),
    )
    metrics[1].metric(
        "With confident coach",
        int(unassigned["assigned_active_or_paused_enrollment_count"]),
    )
    metrics[2].metric(
        "Without coach", int(unassigned["unassigned_active_or_paused_enrollment_count"])
    )
    metrics[3].metric(
        "Ambiguous workbook rows", int(unassigned["ambiguous_workbook_row_count"])
    )
    if coaches.empty:
        st.info("No coach data.")
    else:
        q5 = coaches.assign(
            not_red=lambda d: d["active_or_paused_enrollment_count"]
            - d["red_enrollment_count"]
        )
        stacked = q5.melt(
            id_vars=["coach"],
            value_vars=["red_enrollment_count", "not_red"],
            var_name="ampel",
            value_name="enrollments",
        )
        stacked["ampel"] = stacked["ampel"].map(
            {"red_enrollment_count": "Red", "not_red": "Not red"}
        )
        order = q5.sort_values("active_or_paused_enrollment_count", ascending=False)[
            "coach"
        ].tolist()
        chart = (
            alt.Chart(stacked)
            .mark_bar()
            .encode(
                y=alt.Y("coach:N", sort=order, title=None),
                x=alt.X(
                    "enrollments:Q", title="Active or paused enrollments", stack="zero"
                ),
                color=alt.Color(
                    "ampel:N",
                    scale=alt.Scale(domain=["Red", "Not red"], range=[RED, BLUE]),
                    title=None,
                    sort=["Red", "Not red"],
                ),
                order=alt.Order("ampel:N", sort="ascending"),
                tooltip=["coach", "ampel", "enrollments"],
            )
            .properties(height=max(150, 44 * len(q5)))
        )
        st.altair_chart(chart, width="stretch")
        table = q5.assign(
            red_share=lambda d: d["red_enrollment_count"]
            / d["active_or_paused_enrollment_count"].where(
                d["active_or_paused_enrollment_count"] > 0
            )
        )
        st.dataframe(
            table[
                [
                    "coach",
                    "active_or_paused_enrollment_count",
                    "red_enrollment_count",
                    "red_share",
                    "ambiguous_assignment_count",
                    "unmatched_enrollment_count",
                ]
            ]
            .sort_values("active_or_paused_enrollment_count", ascending=False)
            .rename(
                columns={
                    "active_or_paused_enrollment_count": "active or paused",
                    "red_enrollment_count": "red",
                    "ambiguous_assignment_count": "ambiguous rows",
                    "unmatched_enrollment_count": "unmatched rows",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "red_share": st.column_config.NumberColumn(
                    "red share", format="percent"
                )
            },
        )
    st.caption(
        "Counts distinct enrollments with status aktiv or pausiert that a coach is confidently assigned to (unique e-mail, or "
        "name + training + cohort). Ambiguous and unmatched workbook rows are never forced into an enrollment and never counted as "
        f"assigned. Coach list dated {pd.to_datetime(unassigned['coach_snapshot_as_of_date']):%d.%m.%Y} (snapshot loaded "
        f"{pd.to_datetime(unassigned['coach_snapshot_date']):%d.%m.%Y}); KPI cutoff {reporting_date}."
    )

# ---- Definitions ----------------------------------------------------------------------
with tab6:
    st.markdown(f"""
**Definitions and decisions** (details: `README.md`, model docs in `dbt/models/reporting/_reporting.yml`)

1. **Active** = enrollment status `aktiv`. CRM keeps only the current status, so the status at the cutoff ({reporting_date}) is approximated by the current one.
2. **Completion/abandonment population**: planned end before {reporting_date}. Completed = `abgeschlossen`, abandoned = `abgebrochen`; other statuses stay in the denominator as "other".
3. **Progress** = distinct completed curriculum modules of the enrollment's own training / modules of that training (no event = 0).
4. **Time progress** = elapsed days / planned duration, clamped to 0-100%. **Behind schedule** = progress - time progress <= -15 percentage points.
5. **NPS** = 100 x (promoters - detractors) / valid answers; promoters 9-10, passives 7-8, detractors 0-6; only completion feedback, valid scores 0-10, submitted up to the cutoff.
6. **Coach utilization** counts `aktiv` and `pausiert` enrollments from the latest Betreuungsliste snapshot. Ambiguous and unmatched rows are never force-matched.
7. The Betreuungsliste is dated 01.09.2026, the cutoff is 31.08.2026 (documented one-day mismatch).

**Dashboard-only conventions** (not KPI definitions): the "Where action is needed" box ranks trainings only with at least
{MIN_ACTIVE_FOR_RANKING} active enrollments, {MIN_ELIGIBLE_FOR_RANKING} eligible enrollments or {MIN_NPS_RESPONSES_FOR_RANKING} NPS answers;
groups below {SMALL_GROUP} enrollments are drawn faded in question 2.
""")
