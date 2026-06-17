# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Guard against the canonical DDL (schema.sql) drifting from the ORM models.

Production applies ``avatar/engine/schema.sql`` (reviewed Postgres DDL); tests
and dev bootstrap use ``Base.metadata.create_all``. If the two diverge — as they
did once when ``schema.sql`` was missing the whole ``approvals`` table — a
production deploy gets a different schema than the engine expects. This test
parses the table/column names out of schema.sql and asserts they match the
models exactly, so the drift cannot recur silently.

It is deliberately text-based (not a live Postgres apply) so it runs anywhere,
on every push. Types/defaults are not compared — names and presence catch the
class of drift that actually breaks the engine.
"""

from __future__ import annotations

import re
from pathlib import Path

from avatar.engine.models import Base

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "avatar" / "engine" / "schema.sql"

# Columns present in the models but intentionally derived/absent in the DDL, or
# vice-versa, would be listed here. Empty = exact parity required.
_IGNORE: dict[str, set[str]] = {}


def _parse_schema_sql(text: str) -> dict[str, set[str]]:
    """Return {table_name: {column_names}} from the CREATE TABLE blocks."""
    tables: dict[str, set[str]] = {}
    for m in re.finditer(r"CREATE TABLE (\w+)\s*\((.*?)\n\);", text, re.DOTALL):
        name = m.group(1)
        cols: set[str] = set()
        for raw in m.group(2).splitlines():
            line = raw.strip()
            if not line or line.startswith("--"):
                continue
            # Skip table-level constraints, not column definitions.
            if re.match(r"(UNIQUE|PRIMARY KEY|FOREIGN KEY|CHECK|CONSTRAINT)\b", line, re.I):
                continue
            col = line.split()[0].strip(",")
            if col.isidentifier():
                cols.add(col)
        tables[name] = cols
    return tables


def test_schema_sql_tables_match_models():
    ddl_tables = _parse_schema_sql(SCHEMA_SQL.read_text())
    model_tables = set(Base.metadata.tables)
    assert set(ddl_tables) == model_tables, (
        "schema.sql tables differ from the ORM models: "
        f"only in DDL={set(ddl_tables) - model_tables}, "
        f"only in models={model_tables - set(ddl_tables)}"
    )


def test_schema_sql_uses_portable_text_not_native_enums():
    """The models use portable String for status/type (cross-dialect with
    SQLite). If schema.sql declares native Postgres ENUMs instead, status
    filters fail at runtime ('operator does not exist: run_status = varchar').
    Keep schema.sql free of CREATE TYPE ... ENUM."""
    from avatar.engine.db import _split_sql  # strips comments

    sql = " ".join(_split_sql(SCHEMA_SQL.read_text())).upper()
    assert "CREATE TYPE" not in sql and " ENUM" not in sql, (
        "schema.sql must use `text`, not native ENUMs, to match the String models"
    )


def test_split_sql_handles_inline_comment_semicolons():
    """schema.sql has inline comments containing ';' (e.g. 'heartbeated; NULL').
    The splitter must not truncate a statement there."""
    from avatar.engine.db import _split_sql

    statements = _split_sql(SCHEMA_SQL.read_text())
    # Every CREATE TABLE statement must be whole (balanced parens, not a fragment).
    for s in statements:
        if s.upper().startswith("CREATE TABLE"):
            assert s.count("(") == s.count(")"), f"truncated statement: {s[:80]}…"
    # The runs table is one complete statement reaching its last column.
    runs = [s for s in statements if s.upper().startswith("CREATE TABLE RUNS")]
    assert runs and "updated_at" in runs[0]


def test_schema_sql_columns_match_models():
    ddl_tables = _parse_schema_sql(SCHEMA_SQL.read_text())
    for table_name, table in Base.metadata.tables.items():
        model_cols = {c.name for c in table.columns} - _IGNORE.get(table_name, set())
        ddl_cols = ddl_tables.get(table_name, set()) - _IGNORE.get(table_name, set())
        assert model_cols == ddl_cols, (
            f"column drift in {table_name!r}: "
            f"only in models={model_cols - ddl_cols}, "
            f"only in schema.sql={ddl_cols - model_cols}"
        )
