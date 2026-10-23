"""Validate candidate Olist SQL before it is shown or executed.

This module intentionally separates structural validation from business
judgement.  SQLGlot checks the parsed expression, the DuckDB schema confirms
that referenced data exists, and a small set of explicit Olist rules catches
known aggregation traps.  A passing result is still a candidate query: it is
only ever safe to execute through a read-only DuckDB connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import duckdb

from src.database.load_data import TABLES as OLIST_TABLES
from src.database.read_only import connect_read_only
from src.validation.sql_safety import UnsafeSQLError, ensure_read_only_sql


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "database" / "olist.duckdb"
ALLOWED_TABLES = frozenset(OLIST_TABLES)

# Functions that can read a local file, URL, attached database, extension, or
# other resource outside the allowlisted Olist tables.  The existing safety
# guard covers the common subset; this list keeps the validator defensive even
# if SQLGlot represents a function differently across releases.
_EXTERNAL_ACCESS = re.compile(
    r"\b(?:"
    r"read_[a-z0-9_]*|[a-z0-9_]*_scan|"
    r"glob|http_get|http_post|sniff_csv|query|query_table|"
    r"duckdb_extensions|duckdb_secrets|read_blob|url_decode|shell"
    r")\s*\(",
    flags=re.IGNORECASE,
)
_EXTERNAL_RELATION = re.compile(
    r"\b(FROM|JOIN)\s+(['\"]|https?://|file:|[A-Za-z]:[\\/])",
    flags=re.IGNORECASE,
)
_AGGREGATE_RISK_COLUMNS = {"payment_value", "price", "freight_value"}


@dataclass
class ValidationResult:
    """A UI-friendly result of validating one candidate SQL query."""

    valid: bool
    sql: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema: dict[str, set[str]] = field(default_factory=dict, repr=False)

    @property
    def message(self) -> str:
        """Return concise feedback suitable for a status component."""
        if self.valid:
            return "SQL đã vượt qua các kiểm tra cú pháp, schema và an toàn."
        return self.errors[0] if self.errors else "SQL không vượt qua kiểm tra."


def quote_identifier(identifier: str) -> str:
    """Quote a DuckDB identifier obtained from database metadata."""
    return '"' + identifier.replace('"', '""') + '"'


def load_allowed_schema(db_path: Path = DB_PATH) -> dict[str, set[str]]:
    """Load the Olist table/column allowlist from a read-only DuckDB database."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(
            "Chưa có database Olist. Hãy chạy python -m src.database.load_data trước."
        )

    connection = connect_read_only(db_path)
    try:
        tables = connection.execute("SHOW TABLES").fetchall()
        schema: dict[str, set[str]] = {}
        for (table_name,) in tables:
            table_name = str(table_name).lower()
            if table_name not in ALLOWED_TABLES:
                continue
            columns = connection.execute(
                f"DESCRIBE {quote_identifier(table_name)}"
            ).fetchall()
            schema[table_name] = {
                str(column[0]).lower() for column in columns
            }
        if not schema:
            raise RuntimeError("Database Olist không chứa bảng nào để kiểm tra.")
        return schema
    finally:
        connection.close()


def _normalise_schema(
    schema: Mapping[str, set[str] | list[str] | tuple[str, ...]],
) -> dict[str, set[str]]:
    """Normalize an injected test schema without widening the table allowlist."""
    normalized: dict[str, set[str]] = {}
    for table, columns in schema.items():
        table_name = str(table).lower()
        if table_name not in ALLOWED_TABLES:
            continue
        normalized[table_name] = {str(column).lower() for column in columns}
    return normalized


def _load_sqlglot() -> tuple[Any, Any, type[Exception]]:
    """Import SQLGlot lazily so a missing optional dependency is actionable."""
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.errors import ParseError
    except ImportError as error:
        raise RuntimeError(
            "Thiếu sqlglot. Hãy chạy: python -m pip install -r requirements.txt"
        ) from error
    return sqlglot, exp, ParseError


def _blocked_expression_types(exp: Any) -> tuple[type[Any], ...]:
    """Get known mutation/control nodes without coupling to one SQLGlot version."""
    names = (
        "Alter",
        "Attach",
        "Command",
        "Copy",
        "Create",
        "Delete",
        "Detach",
        "Drop",
        "Execute",
        "Grant",
        "Insert",
        "LoadData",
        "Merge",
        "Pragma",
        "Revoke",
        "Set",
        "TruncateTable",
        "Update",
        "Use",
    )
    return tuple(node for name in names if (node := getattr(exp, name, None)) is not None)


def _is_query_expression(statement: Any, exp: Any) -> bool:
    """Allow SELECT/CTE/set-operation queries, not commands masquerading as SQL."""
    query_type = getattr(exp, "Query", None)
    if query_type is not None and isinstance(statement, query_type):
        return True
    fallback_types = tuple(
        expression_type
        for expression_type in (
            getattr(exp, "Select", None),
            getattr(exp, "Subqueryable", None),
        )
        if expression_type is not None
    )
    return bool(fallback_types) and isinstance(statement, fallback_types)


def _cte_names(statement: Any, exp: Any) -> set[str]:
    return {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _table_aliases(statement: Any, exp: Any, allowed_schema: Mapping[str, set[str]]) -> dict[str, str]:
    """Map base-table aliases to their allowlisted table names."""
    aliases: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        table_name = table.name.lower() if table.name else ""
        if table_name in allowed_schema:
            aliases[table.alias_or_name.lower()] = table_name
            aliases[table_name] = table_name
    return aliases


def _derived_table_aliases(statement: Any, exp: Any) -> set[str]:
    """Return aliases whose output columns must be resolved by DuckDB.

    A derived table is not part of the physical Olist allowlist, but it is a
    safe SQL construct when its inner query is safe.  Its projected columns
    have scope-dependent names, so the read-only ``EXPLAIN`` pass is the
    authoritative validator for references such as ``payment_by_order.total``.
    """
    aliases: set[str] = set()
    for subquery in statement.find_all(exp.Subquery):
        alias = subquery.alias_or_name
        if alias:
            aliases.add(alias.lower())
    return aliases


def _validate_table_references(
    statement: Any,
    exp: Any,
    allowed_schema: Mapping[str, set[str]],
    errors: list[str],
) -> set[str]:
    """Reject databases/schemas and tables outside the current Olist allowlist."""
    cte_names = _cte_names(statement, exp)
    referenced_tables: set[str] = set()
    for table in statement.find_all(exp.Table):
        table_name = table.name.lower() if table.name else ""
        if table.catalog or table.db:
            errors.append(
                "Không cho phép truy cập catalog/schema bên ngoài phạm vi Olist: "
                f"{table.sql(dialect='duckdb')}"
            )
            continue
        if table_name in cte_names:
            continue
        if table_name not in allowed_schema:
            errors.append(f"Bảng không tồn tại hoặc không được phép: {table.name}")
            continue
        referenced_tables.add(table_name)
    return referenced_tables


def _projection_aliases(statement: Any, exp: Any) -> set[str]:
    aliases: set[str] = set()
    for select in statement.find_all(exp.Select):
        for projection in select.expressions:
            # ``alias_or_name`` returns a plain column name too, which would
            # incorrectly exempt every selected source column from validation.
            alias_expression = projection.args.get("alias")
            if alias_expression is not None and alias_expression.name:
                aliases.add(alias_expression.name.lower())
    return aliases


def _validate_column_references(
    statement: Any,
    exp: Any,
    allowed_schema: Mapping[str, set[str]],
    referenced_tables: set[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Check columns against base tables while leaving CTE scopes to DuckDB."""
    cte_names = _cte_names(statement, exp)
    aliases = _table_aliases(statement, exp, allowed_schema)
    derived_aliases = _derived_table_aliases(statement, exp)
    projection_aliases = _projection_aliases(statement, exp)

    for column in statement.find_all(exp.Column):
        column_name = column.name.lower() if column.name else ""
        if not column_name or column_name == "*":
            continue
        qualifier = column.table.lower() if column.table else ""
        if qualifier:
            if qualifier in cte_names or qualifier in derived_aliases:
                # DuckDB EXPLAIN below knows CTE and derived-table output
                # shapes, which cannot be inferred from the physical schema.
                continue
            base_table = aliases.get(qualifier)
            if base_table is None:
                errors.append(
                    f"Bảng hoặc alias không tồn tại cho cột {column.sql(dialect='duckdb')}."
                )
            elif column_name not in allowed_schema[base_table]:
                errors.append(
                    f"Cột không tồn tại: {column.sql(dialect='duckdb')} "
                    f"(bảng {base_table})."
                )
            continue

        if column_name in projection_aliases:
            continue
        candidate_tables = {
            table
            for table in referenced_tables
            if column_name in allowed_schema.get(table, set())
        }
        if not candidate_tables and referenced_tables:
            errors.append(f"Cột không tồn tại trong các bảng được truy vấn: {column.name}")
        elif len(candidate_tables) > 1:
            warnings.append(
                f"Cột {column.name} có thể mơ hồ giữa các bảng {', '.join(sorted(candidate_tables))}."
            )


def _direct_source_tables(select: Any, exp: Any) -> set[str]:
    """Return base tables directly joined by one SELECT scope."""
    tables: set[str] = set()
    sources = []
    from_expression = select.args.get("from")
    if from_expression is not None:
        sources.append(from_expression)
    sources.extend(select.args.get("joins") or [])
    for source in sources:
        for table in source.find_all(exp.Table):
            if table.name:
                tables.add(table.name.lower())
    return tables


def _columns_in_expression(expression: Any, exp: Any) -> set[str]:
    return {
        column.name.lower()
        for column in expression.find_all(exp.Column)
        if column.name
    }


def _business_rule_checks(statement: Any, exp: Any) -> tuple[list[str], list[str]]:
    """Apply explicit, conservative Olist business-rule guardrails.

    These rules deliberately reject only a well-known multiplication error.
    Other potentially ambiguous definitions become warnings because SQL alone
    cannot know whether the user's wording meant "unique customers" or a
    different analytical measure.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for select in statement.find_all(exp.Select):
        source_tables = _direct_source_tables(select, exp)
        if {"order_items", "order_payments"}.issubset(source_tables):
            for aggregate in select.find_all(exp.Sum):
                risk_columns = _columns_in_expression(aggregate, exp) & _AGGREGATE_RISK_COLUMNS
                if risk_columns:
                    errors.append(
                        "Quy tắc nghiệp vụ: không được JOIN trực tiếp order_items và "
                        "order_payments rồi SUM "
                        f"{', '.join(sorted(risk_columns))}. Hãy tổng hợp từng bảng về "
                        "một dòng mỗi order_id trước khi JOIN."
                    )
                    break

    sql_text = statement.sql(dialect="duckdb").lower()
    if "count(distinct" in sql_text and re.search(r"count\s*\(\s*distinct\s+[^)]*customer_id", sql_text):
        warnings.append(
            "Nếu chỉ số là khách hàng duy nhất hoặc khách mua lại, hãy dùng "
            "customers.customer_unique_id thay vì customer_id."
        )
    if re.search(r"group\s+by[^;]*\bcustomer_id\b", sql_text) and "having" in sql_text:
        warnings.append(
            "Nếu đang xác định khách mua lại, hãy GROUP BY customer_unique_id, không chỉ customer_id."
        )
    if "order_delivered_customer_date is not null" in sql_text and "order_status" not in sql_text:
        warnings.append(
            "Không suy ra đơn đã giao chỉ từ thời điểm giao; dùng order_status = 'delivered' "
            "khi câu hỏi nói về đơn đã giao."
        )
    return errors, warnings


def _duckdb_explain(sql: str, db_path: Path) -> str | None:
    """Ask read-only DuckDB to bind the safe query without executing its rows."""
    try:
        connection = connect_read_only(db_path)
    except duckdb.Error as error:
        return f"Không thể mở database chỉ đọc để kiểm tra: {error}"
    try:
        connection.execute(f"EXPLAIN {sql}").fetchall()
    except duckdb.Error as error:
        return f"DuckDB không thể xác thực query: {error}"
    finally:
        connection.close()
    return None


def validate_sql(
    sql: str,
    *,
    db_path: Path = DB_PATH,
    schema: Mapping[str, set[str] | list[str] | tuple[str, ...]] | None = None,
) -> ValidationResult:
    """Validate one candidate DuckDB query without executing it.

    The result never raises for an invalid LLM response.  ``schema`` exists to
    support isolated tests, but can only narrow the physical Olist table
    allowlist; it can never authorize additional tables. Callers must execute
    only ``result.sql`` when ``result.valid`` is true, and still use a
    read-only DuckDB connection for the actual query.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        safe_sql = ensure_read_only_sql(sql)
    except UnsafeSQLError as error:
        return ValidationResult(valid=False, errors=[str(error)])

    if _EXTERNAL_ACCESS.search(safe_sql) or _EXTERNAL_RELATION.search(safe_sql):
        return ValidationResult(
            valid=False,
            sql=safe_sql,
            errors=["Không cho phép truy cập tệp, URL hoặc nguồn dữ liệu bên ngoài."],
        )

    try:
        sqlglot, exp, parse_error = _load_sqlglot()
    except RuntimeError as error:
        return ValidationResult(valid=False, sql=safe_sql, errors=[str(error)])

    try:
        statements = sqlglot.parse(safe_sql, read="duckdb")
    except parse_error as error:
        return ValidationResult(
            valid=False,
            sql=safe_sql,
            errors=[f"Cú pháp SQL không hợp lệ: {error}"],
        )
    if len(statements) != 1:
        return ValidationResult(
            valid=False,
            sql=safe_sql,
            errors=["Chỉ cho phép đúng một câu truy vấn SQL."],
        )

    statement = statements[0]
    if not _is_query_expression(statement, exp):
        errors.append("Chỉ cho phép truy vấn SELECT hoặc WITH ... SELECT.")
    blocked_types = _blocked_expression_types(exp)
    if blocked_types and any(statement.find(node_type) is not None for node_type in blocked_types):
        errors.append("Không cho phép lệnh thay đổi dữ liệu, cấu trúc hoặc phiên làm việc.")
    if errors:
        return ValidationResult(valid=False, sql=safe_sql, errors=errors)

    try:
        allowed_schema = _normalise_schema(schema) if schema is not None else load_allowed_schema(db_path)
    except (FileNotFoundError, RuntimeError, duckdb.Error) as error:
        return ValidationResult(valid=False, sql=safe_sql, errors=[str(error)])

    referenced_tables = _validate_table_references(
        statement, exp, allowed_schema, errors
    )
    _validate_column_references(
        statement,
        exp,
        allowed_schema,
        referenced_tables,
        errors,
        warnings,
    )
    business_errors, business_warnings = _business_rule_checks(statement, exp)
    errors.extend(business_errors)
    warnings.extend(business_warnings)

    if not errors and schema is None:
        explain_error = _duckdb_explain(safe_sql, Path(db_path))
        if explain_error:
            errors.append(explain_error)

    return ValidationResult(
        valid=not errors,
        sql=safe_sql,
        errors=errors,
        warnings=warnings,
        schema=allowed_schema,
    )
