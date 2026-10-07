"""Idempotency check: build twice and prove that the second build changes nothing.

For every relation in the schemas staging, intermediate, marts and reporting the script records
the row count and an order-independent content checksum after each build and fails when a count
or checksum differs, or when a grain is violated (duplicate keys).
"""

import argparse
import os
import subprocess
import sys

import duckdb

SCHEMAS = ("staging", "intermediate", "marts", "reporting")
GRAINS = {
    "staging.stg_lxp__trainings": ["training_id"],
    "staging.stg_lxp__training_modules": ["training_id", "module_id"],
    "staging.stg_crm__participants": ["participant_id"],
    "staging.stg_crm__enrollments": ["enrollment_id"],
    "staging.stg_lxp__progress_events": ["event_id"],
    "staging.stg_lxp__survey_responses": ["response_id"],
    "staging.stg_coach__assignments": ["coach_assignment_id", "snapshot_date"],
    "marts.fct_enrollments": ["enrollment_id"],
    "marts.fct_enrollment_progress": ["enrollment_id"],
    "marts.fct_coach_assignments": ["coach_assignment_id", "snapshot_date"],
    "reporting.rpt_nps_by_training_quarter": ["training_id", "feedback_quarter"],
    "reporting.rpt_active_enrollments_by_training": ["training_id"],
}


def snapshot(database: str) -> dict:
    connection = duckdb.connect(database, read_only=True)
    result = {}
    relations = connection.execute(
        "select table_schema, table_name from information_schema.tables "
        "where table_schema in ({}) order by 1, 2".format(
            ", ".join(f"'{s}'" for s in SCHEMAS)
        )
    ).fetchall()
    for schema, name in relations:
        relation = f"{schema}.{name}"
        count, checksum = connection.execute(
            f"select count(*), coalesce(sum(hash(t)::hugeint), 0) from {relation} t"
        ).fetchone()
        entry = {"rows": count, "checksum": checksum}
        if relation in GRAINS:
            keys = ", ".join(GRAINS[relation])
            entry["duplicate_keys"] = connection.execute(
                f"select count(*) from (select {keys} from {relation} group by {keys} having count(*) > 1)"
            ).fetchone()[0]
        result[relation] = entry
    connection.close()
    return result


def build(dbt: str, dbt_dir: str, database: str) -> None:
    env = {**os.environ, "DBT_PROFILES_DIR": ".", "STACKFUEL_DUCKDB_PATH": database}
    completed = subprocess.run(
        [dbt, "build"], cwd=dbt_dir, env=env, capture_output=True, text=True
    )
    summary = [line for line in completed.stdout.splitlines() if "Done." in line]
    print(
        "  dbt build:",
        summary[-1].split("Done.")[-1].strip() if summary else "no summary",
    )
    if completed.returncode != 0:
        print(completed.stdout)
        raise SystemExit("dbt build failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--dbt-dir", default="dbt")
    parser.add_argument("--dbt", default="dbt")
    args = parser.parse_args()

    print("Build 1")
    build(args.dbt, args.dbt_dir, args.database)
    first = snapshot(args.database)
    print("Build 2")
    build(args.dbt, args.dbt_dir, args.database)
    second = snapshot(args.database)

    problems = []
    if set(first) != set(second):
        problems.append(f"relations differ: {sorted(set(first) ^ set(second))}")
    for relation in sorted(set(first) & set(second)):
        a, b = first[relation], second[relation]
        if a["rows"] != b["rows"]:
            problems.append(f"{relation}: row count {a['rows']} -> {b['rows']}")
        elif a["checksum"] != b["checksum"]:
            problems.append(f"{relation}: content changed between builds")
        if b.get("duplicate_keys"):
            problems.append(f"{relation}: {b['duplicate_keys']} duplicate keys")
    width = max(len(name) for name in second)
    for relation, entry in sorted(second.items()):
        marker = "ok" if first[relation] == entry else "CHANGED"
        print(f"  {relation:<{width}}  rows={entry['rows']:>6}  {marker}")
    if problems:
        print("FAILED:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print(
        f"OK: {len(second)} relations identical after the second build, no duplicate keys"
    )


if __name__ == "__main__":
    main()
