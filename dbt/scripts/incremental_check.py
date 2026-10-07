"""Proves that the incremental staging models equal a full refresh.

Works on two scratch copies of the database (the real database is only read):

1. Both copies get a baseline `dbt build`.
2. Two new curated snapshot days are injected one after the other into `raw.progress_events_curated`
   and `raw.survey_responses_curated` of both copies: new records, re-sent identical records,
   records whose content changed at the source, and hard deletes recorded in
   `raw_log.source_record_state` for records that are NOT part of the new day.
3. After each day copy A is built incrementally (`dbt build`), copy B from scratch
   (`dbt build --full-refresh`). Row counts and content checksums of all relations must be
   identical, and the new rows, the changed rows and the deletion flags must be visible.
4. Before the second day a marker is written into a staging row of A that comes from the oldest
   snapshot day. It must survive: the incremental run starts at the newest loaded day (the
   baseline day itself is re-read on purpose, older days are not).
5. A further incremental run on the same day must change nothing.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta

import duckdb

from rerun_check import snapshot

MARKER = "INCREMENTAL-CHECK-MARKER"


def dbt(binary: str, dbt_dir: str, database: str, *args: str) -> None:
    env = {**os.environ, "DBT_PROFILES_DIR": ".", "STACKFUEL_DUCKDB_PATH": database}
    completed = subprocess.run(
        [binary, "build", *args], cwd=dbt_dir, env=env, capture_output=True, text=True
    )
    summary = [line for line in completed.stdout.splitlines() if "Done." in line]
    label = " ".join(args) or "(incremental)"
    print(
        f"  dbt build {label}: {summary[-1].split('Done.')[-1].strip() if summary else 'no summary'}"
    )
    if completed.returncode != 0:
        print(completed.stdout)
        raise SystemExit("dbt build failed")


def pick(connection, table: str, key: str, limit: int, offset: int) -> str:
    """Deterministic, repeatable selection of source IDs as a SQL list."""
    rows = connection.execute(
        f"select {key} from (select distinct {key} from {table}) order by hash({key}), {key} limit {limit} offset {offset}"
    ).fetchall()
    return ", ".join("'" + row[0].replace("'", "''") + "'" for row in rows)


def inject_new_day(database: str, shift: int) -> dict:
    connection = duckdb.connect(database)
    last_day = connection.execute(
        "select max(snapshot_date) from raw.progress_events_curated"
    ).fetchone()[0]
    day = last_day + timedelta(days=1)
    plan = {"day": day}
    specs = (
        (
            "raw.progress_events_curated",
            "event_id",
            "progress_events",
            "event_at_utc + interval 1 hour as event_at_utc, event_at + interval 1 hour as event_at",
        ),
        (
            "raw.survey_responses_curated",
            "response_id",
            "survey_responses",
            "'changed at source' as answer_context",
        ),
    )
    for table, key, source_object, change in specs:
        common = (
            f"date '{day}' as snapshot_date, snapshot_at + interval 1 day as snapshot_at, "
            f"ingested_at + interval 1 day as ingested_at, 'run-incremental-check' as run_id"
        )
        new_ids, resent_ids, changed_ids, deleted_ids = (
            pick(connection, table, key, 40, shift),
            pick(connection, table, key, 20, shift + 40),
            pick(connection, table, key, 10, shift + 60),
            pick(connection, table, key, 5, shift + 100),
        )
        connection.execute(  # new records
            f"insert into {table} select * replace ('SYN{shift}-' || {key} as {key}, 'syn-' || payload_hash as payload_hash, "
            f"{common}) from {table} where {key} in ({new_ids})"
        )
        connection.execute(  # re-sent, unchanged
            f"insert into {table} select * replace ({common}) from {table} where {key} in ({resent_ids})"
        )
        connection.execute(  # changed at the source
            f"insert into {table} select * replace ({common}, {change}) from {table} where {key} in ({changed_ids})"
        )
        connection.execute(  # hard deleted records are not part of the new day
            "update raw_log.source_record_state set is_deleted = true, deleted_at = now(), deletion_detected_at = now() "
            f"where source_object = '{source_object}' and source_id in ({deleted_ids})"
        )
        plan[source_object] = {
            "changed": changed_ids,
            "deleted": deleted_ids,
            "all": ", ".join([new_ids, resent_ids, changed_ids, deleted_ids]),
        }
    connection.close()
    return plan


def check_phase(
    copy_a: str, copy_b: str, plan: dict, phase: int, problems: list
) -> dict:
    connection = duckdb.connect(copy_a)
    events, responses = plan["progress_events"], plan["survey_responses"]
    checks = {
        "new events visible": (
            "select count(*) from staging.stg_lxp__progress_events where event_id like 'SYN%'",
            40 * phase,
        ),
        "new responses visible": (
            "select count(*) from staging.stg_lxp__survey_responses where response_id like 'SYN%'",
            40 * phase,
        ),
        "changed responses replaced": (
            f"select count(*) from staging.stg_lxp__survey_responses where response_id in ({responses['changed']}) "
            f"and snapshot_date = date '{plan['day']}' and answer_context = 'changed at source'",
            10,
        ),
        "changed events replaced": (
            f"select count(*) from staging.stg_lxp__progress_events where event_id in ({events['changed']}) "
            f"and snapshot_date = date '{plan['day']}'",
            10,
        ),
        "event deletions flagged on old rows": (
            f"select count(*) from staging.stg_lxp__progress_events where is_deleted and event_id in ({events['deleted']})",
            5,
        ),
        "response deletions flagged on old rows": (
            f"select count(*) from staging.stg_lxp__survey_responses where is_deleted and response_id in ({responses['deleted']})",
            5,
        ),
    }
    for name, (query, expected) in checks.items():
        actual = connection.execute(query).fetchone()[0]
        print(f"  {name}: {actual} (expected {expected})")
        if actual != expected:
            problems.append(f"day {plan['day']}: {name}: {actual} != {expected}")
    connection.close()

    incremental, full = snapshot(copy_a), snapshot(copy_b)
    if set(incremental) != set(full):
        problems.append(f"relations differ: {sorted(set(incremental) ^ set(full))}")
    for relation in sorted(set(incremental) & set(full)):
        if incremental[relation] != full[relation]:
            problems.append(
                f"day {plan['day']}: {relation}: incremental {incremental[relation]} != full refresh {full[relation]}"
            )
    print(
        f"  incremental == full refresh: {sum(incremental.get(r) == full[r] for r in full)} of {len(full)} relations identical"
    )
    return incremental


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--dbt-dir", default="dbt")
    parser.add_argument("--dbt", default="dbt")
    args = parser.parse_args()

    workdir = tempfile.mkdtemp(prefix="stackfuel-incremental-")
    copy_a, copy_b = os.path.join(workdir, "incremental.duckdb"), os.path.join(
        workdir, "full_refresh.duckdb"
    )
    problems = []
    try:
        shutil.copyfile(args.database, copy_a)
        shutil.copyfile(args.database, copy_b)

        print("Baseline build on both copies")
        dbt(args.dbt, args.dbt_dir, copy_a)
        dbt(args.dbt, args.dbt_dir, copy_b)

        result = {}
        for phase, shift in ((1, 0), (2, 200)):
            print(
                f"Day {phase}: inject a new curated snapshot day (new, re-sent, changed records, hard deletes)"
            )
            plan = inject_new_day(copy_a, shift)
            inject_new_day(copy_b, shift)
            print(f"  new snapshot day: {plan['day']}")

            marker = None
            if phase == 2:
                # a staging row of the oldest snapshot day that this day does not touch
                connection = duckdb.connect(copy_a)
                marker = connection.execute(
                    "select event_id, source_timezone from staging.stg_lxp__progress_events "
                    "where snapshot_date = (select min(snapshot_date) from staging.stg_lxp__progress_events) "
                    f"and event_id not in ({plan['progress_events']['all']}) order by event_id limit 1 offset 300"
                ).fetchone()
                connection.execute(
                    "update staging.stg_lxp__progress_events set source_timezone = ? where event_id = ?",
                    [MARKER, marker[0]],
                )
                connection.close()

            print("  copy A: incremental run, copy B: full refresh")
            dbt(args.dbt, args.dbt_dir, copy_a)
            dbt(args.dbt, args.dbt_dir, copy_b, "--full-refresh")

            if marker:
                connection = duckdb.connect(copy_a)
                kept = connection.execute(
                    "select count(*) from staging.stg_lxp__progress_events where event_id = ? and source_timezone = ?",
                    [marker[0], MARKER],
                ).fetchone()[0]
                print(
                    f"  row of the oldest snapshot day was not re-read: {'yes' if kept == 1 else 'NO'}"
                )
                if kept != 1:
                    problems.append(
                        "incremental run re-read snapshot days older than the newest loaded day"
                    )
                connection.execute(
                    "update staging.stg_lxp__progress_events set source_timezone = ? where event_id = ?",
                    [marker[1], marker[0]],
                )
                connection.close()
            result = check_phase(copy_a, copy_b, plan, phase, problems)

        print("Copy A: another incremental run on the same day (must change nothing)")
        dbt(args.dbt, args.dbt_dir, copy_a)
        again = snapshot(copy_a)
        for relation in sorted(again):
            if again[relation] != result[relation]:
                problems.append(
                    f"{relation}: repeated incremental run changed the relation"
                )

        if problems:
            print("FAILED:\n  " + "\n  ".join(problems))
            sys.exit(1)
        print(
            f"OK: incremental == full refresh for all {len(result)} relations after two new days, older days were "
            "not re-read, changes and deletes were applied, a rerun changes nothing"
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
