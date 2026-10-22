"""Small defensive checks for SQL proposed by an LLM.

This module is intentionally conservative.  It is an early guardrail, not a
complete SQL parser or authorization system.  The Streamlit executor also
requires the full SQL validator and a DuckDB connection that is read-only with
external-resource access disabled.
"""

from __future__ import annotations

import re


class UnsafeSQLError(ValueError):
    """Raised when a candidate SQL statement is not clearly read-only."""


_BLOCKED_KEYWORDS = re.compile(
    r"\b("
    r"ALTER|ANALYZE|ATTACH|BEGIN|CALL|CHECKPOINT|COMMIT|COPY|CREATE|"
    r"DELETE|DETACH|DROP|EXPORT|GRANT|IMPORT|INSERT|INSTALL|LOAD|MERGE|"
    r"PRAGMA|REASSIGN|REINDEX|REPLACE|REVOKE|ROLLBACK|SET|TRANSACTION|"
    r"TRUNCATE|UPDATE|USE|VACUUM"
    r")\b",
    flags=re.IGNORECASE,
)
_EXTERNAL_READ_FUNCTIONS = re.compile(
    r"\b("
    r"glob|http_get|read_csv|read_csv_auto|read_json|read_ndjson|"
    r"read_parquet|read_text|read_xlsx|sniff_csv"
    r")\s*\(",
    flags=re.IGNORECASE,
)
_LEADING_QUERY = re.compile(r"^\s*(SELECT\b|WITH\b)", flags=re.IGNORECASE)


def strip_sql_comments(sql: str) -> str:
    """Remove simple line/block comments before applying a conservative policy."""
    without_block_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\r\n]*", " ", without_block_comments)


def ensure_read_only_sql(sql: str) -> str:
    """Validate one SELECT/CTE query and return its normalized text.

    Multiple statements are rejected even when every statement happens to be a
    SELECT.  A terminal semicolon is accepted and removed so callers can pass
    the result to an executor that requires one statement.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise UnsafeSQLError("SQL phải là một câu truy vấn không rỗng.")

    cleaned = strip_sql_comments(sql).strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if ";" in cleaned:
        raise UnsafeSQLError("Chỉ cho phép một câu truy vấn SQL.")
    if not _LEADING_QUERY.match(cleaned):
        raise UnsafeSQLError("Chỉ cho phép truy vấn bắt đầu bằng SELECT hoặc WITH.")

    blocked = _BLOCKED_KEYWORDS.search(cleaned)
    if blocked:
        raise UnsafeSQLError(f"Từ khóa không được phép: {blocked.group(0).upper()}")
    external_read = _EXTERNAL_READ_FUNCTIONS.search(cleaned)
    if external_read:
        raise UnsafeSQLError(
            f"Không cho phép đọc tệp/nguồn ngoài: {external_read.group(1)}"
        )
    return cleaned
