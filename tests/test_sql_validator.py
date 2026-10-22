"""Tests for syntax, schema, safety, and Olist business-rule validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.database.read_only import SAFE_READ_ONLY_CONFIG, connect_read_only
from src.validation.sql_validator import load_allowed_schema, validate_sql


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "database" / "olist.duckdb"
pytestmark = pytest.mark.requires_olist_database


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(DISTINCT order_id) AS total_orders FROM orders",
        "SELECT COUNT(*) FROM orders WHERE order_status = 'delivered'",
        """
        WITH payment_by_order AS (
            SELECT order_id, SUM(payment_value) AS total_payment
            FROM order_payments
            GROUP BY order_id
        )
        SELECT SUM(total_payment) AS total_payment
        FROM payment_by_order
        """,
        """
        SELECT SUM(payment_by_order.payment_value) AS total_payment
        FROM (
            SELECT order_id, SUM(payment_value) AS payment_value
            FROM order_payments
            GROUP BY order_id
        ) AS payment_by_order
        """,
    ],
)
def test_valid_read_only_queries_pass(sql: str) -> None:
    result = validate_sql(sql)

    assert result.valid is True
    assert result.sql
    assert result.errors == []


def test_schema_is_read_from_read_only_database() -> None:
    schema = load_allowed_schema()

    assert "orders" in schema
    assert "order_id" in schema["orders"]


def test_all_benchmark_reference_queries_validate() -> None:
    questions = json.loads((ROOT / "benchmarks" / "questions.json").read_text(encoding="utf-8"))

    invalid = [
        question["id"]
        for question in questions
        if not validate_sql(question["reference_sql"]).valid
    ]

    assert invalid == []


def test_read_only_connection_disables_external_access() -> None:
    connection = connect_read_only(DB_PATH)
    try:
        external_access = connection.execute(
            "SELECT current_setting('enable_external_access')"
        ).fetchone()[0]
    finally:
        connection.close()

    assert SAFE_READ_ONLY_CONFIG["enable_external_access"] is False
    assert external_access is False


def test_unknown_column_is_rejected() -> None:
    result = validate_sql("SELECT revenue FROM orders")

    assert result.valid is False
    assert any("Cột không tồn tại" in error for error in result.errors)


def test_unknown_table_is_rejected() -> None:
    result = validate_sql("SELECT * FROM made_up_orders")

    assert result.valid is False
    assert any("Bảng không tồn tại" in error for error in result.errors)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT FROM orders",
        "SELEKT order_id FROM orders",
        "SELECT order_id FROM orders WHERE (",
    ],
)
def test_invalid_syntax_is_rejected(sql: str) -> None:
    result = validate_sql(sql)

    assert result.valid is False
    assert result.errors


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE orders",
        "UPDATE orders SET order_status = 'delivered'",
        "SELECT * FROM orders; SELECT * FROM customers",
        "SELECT * FROM read_csv_auto('C:/outside.csv')",
        "SELECT * FROM http_get('https://example.invalid/data')",
        "SELECT * FROM query_table('orders')",
        "SELECT * FROM sqlite_scan('C:/outside.db', 'orders')",
    ],
)
def test_mutation_multistatement_and_external_access_are_rejected(sql: str) -> None:
    result = validate_sql(sql)

    assert result.valid is False
    assert result.errors


def test_joining_two_detail_tables_then_summing_payment_is_rejected() -> None:
    result = validate_sql(
        """
        SELECT SUM(op.payment_value) AS total_payment
        FROM orders AS o
        JOIN order_items AS oi ON oi.order_id = o.order_id
        JOIN order_payments AS op ON op.order_id = o.order_id
        """
    )

    assert result.valid is False
    assert any("order_items" in error and "order_payments" in error for error in result.errors)


def test_customer_id_unique_count_warns_about_business_identifier() -> None:
    result = validate_sql("SELECT COUNT(DISTINCT customer_id) FROM customers")

    assert result.valid is True
    assert any("customer_unique_id" in warning for warning in result.warnings)


def test_cte_columns_are_bound_by_duckdb() -> None:
    result = validate_sql(
        """
        WITH customer_orders AS (
            SELECT customer_id, COUNT(*) AS order_count
            FROM orders
            GROUP BY customer_id
        )
        SELECT COUNT(*)
        FROM customer_orders
        WHERE order_count > 1
        """
    )

    assert result.valid is True


def test_invalid_qualified_cte_column_is_rejected_by_duckdb_binding() -> None:
    result = validate_sql(
        """
        WITH customer_orders AS (
            SELECT customer_id, COUNT(*) AS order_count
            FROM orders
            GROUP BY customer_id
        )
        SELECT customer_orders.not_an_output_column
        FROM customer_orders
        """
    )

    assert result.valid is False
    assert any("DuckDB" in error for error in result.errors)


def test_schema_qualified_table_is_outside_allowlist() -> None:
    result = validate_sql("SELECT * FROM main.orders")

    assert result.valid is False
    assert any("catalog/schema" in error for error in result.errors)
