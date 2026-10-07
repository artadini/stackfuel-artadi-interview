"""One column specification, three enforcement points.

The curated column contract in ``config.CURATED_COLUMNS`` is a list of ``(name, DuckDB type)``.
From it this module derives

* the DuckDB ``CREATE TABLE`` statement (types and NOT NULL constraints in the database),
* the Arrow schema, i.e. the Parquet type system, that every batch is converted with, and
* :func:`enforce_schema`, which converts Python rows with that schema and sends the result
  through an in-memory Parquet round trip.

Nothing is written to disk: the Parquet bytes only live in memory and prove that the batch
is representable under exactly this schema (types, decimal precision, time zone, nullability).
"""

import re

import pyarrow as pa
import pyarrow.parquet as pq

from .config import NOT_NULL_COLUMNS

from ..helpers.extraction_types import (
    SchemaViolation,
)

_DECIMAL = re.compile(r"^DECIMAL\((\d+),\s*(\d+)\)$")
_ARROW_TYPES = {
    "VARCHAR": pa.string(),
    "JSON": pa.string(),  # JSON text; DuckDB column type is JSON
    "INTEGER": pa.int32(),
    "BIGINT": pa.int64(),
    "BOOLEAN": pa.bool_(),
    "DATE": pa.date32(),
    "TIMESTAMP": pa.timestamp("us"),
    "TIMESTAMPTZ": pa.timestamp("us", tz="UTC"),
    "VARCHAR[]": pa.list_(pa.string()),
}


def arrow_type(sql_type: str) -> pa.DataType:
    match = _DECIMAL.match(sql_type)
    if match:
        return pa.decimal128(int(match.group(1)), int(match.group(2)))
    try:
        return _ARROW_TYPES[sql_type]
    except KeyError:
        raise ValueError(f"No Arrow mapping for SQL type {sql_type!r}") from None


def arrow_schema(columns: list[tuple[str, str]]) -> pa.Schema:
    return pa.schema(
        pa.field(name, arrow_type(sql_type), nullable=name not in NOT_NULL_COLUMNS)
        for name, sql_type in columns
    )


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def create_table_sql(qualified_name: str, columns: list[tuple[str, str]]) -> str:
    definitions = ", ".join(
        f"{quote(name)} {sql_type}" + (" NOT NULL" if name in NOT_NULL_COLUMNS else "")
        for name, sql_type in columns
    )
    return f"CREATE OR REPLACE TABLE {qualified_name} ({definitions})"


def enforce_schema(rows: list[dict], columns: list[tuple[str, str]]) -> pa.Table:
    """Convert ``rows`` to an Arrow table with the exact curated schema or raise.

    Strict on purpose: unknown or missing keys, values of the wrong type, NULLs in NOT NULL
    columns and anything that does not survive a Parquet round trip are all errors.
    """
    schema = arrow_schema(columns)
    expected = set(schema.names)
    list_columns = [name for name, sql_type in columns if sql_type.endswith("[]")]
    for number, row in enumerate(rows, start=1):
        keys = set(row)
        if keys != expected:
            raise SchemaViolation(
                f"row {number}: missing={sorted(expected - keys)} unexpected={sorted(keys - expected)}"
            )
        for (
            name
        ) in (
            list_columns
        ):  # Arrow would silently turn a string into a list of characters
            if row[name] is not None and not isinstance(row[name], (list, tuple)):
                raise SchemaViolation(
                    f"row {number}: {name} must be a list, got {type(row[name]).__name__}"
                )
    try:
        table = pa.Table.from_pylist(rows, schema=schema)
        table.validate(full=True)
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink, compression="zstd")
        round_tripped = pq.read_table(pa.BufferReader(sink.getvalue()))
    except (
        pa.ArrowInvalid,
        pa.ArrowTypeError,
        pa.ArrowNotImplementedError,
        OverflowError,
    ) as error:
        raise SchemaViolation(str(error)) from error
    if not round_tripped.schema.equals(schema, check_metadata=False):
        raise SchemaViolation(
            f"Parquet round trip changed the schema:\n{round_tripped.schema}"
        )
    return round_tripped
