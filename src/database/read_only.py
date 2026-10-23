"""Safe read-only DuckDB connections used by the query-facing application."""

from __future__ import annotations

from pathlib import Path

import duckdb


# `read_only=True` protects the Olist database itself. These settings close a
# separate path: DuckDB table functions and extensions must not read files,
# contact URLs, or attach other data sources on behalf of generated SQL. The
# memory ceiling also prevents an otherwise read-only generated query from
# consuming all memory in the application process.
SAFE_READ_ONLY_CONFIG = {
    "enable_external_access": False,
    "memory_limit": "512MB",
}


def connect_read_only(db_path: Path | str) -> duckdb.DuckDBPyConnection:
    """Open a local DuckDB file without write or external-resource access."""
    return duckdb.connect(
        str(Path(db_path)),
        read_only=True,
        config=SAFE_READ_ONLY_CONFIG.copy(),
    )
