"""Safety-focused tests for the Streamlit application's query pipeline."""

from __future__ import annotations

import app
import pandas as pd
import pytest
import src.llm.text_to_sql as text_to_sql
import src.validation.sql_validator as sql_validator
from src.validation.sql_validator import ValidationResult


@pytest.mark.requires_olist_database
def test_execute_read_only_query_returns_olist_results() -> None:
    dataframe, truncated = app.execute_read_only_query(
        "SELECT COUNT(*) AS total_orders FROM orders"
    )

    assert truncated is False
    assert dataframe.loc[0, "total_orders"] == 99_441


def test_chart_is_shown_only_for_a_small_metric_result() -> None:
    chart_columns = app._choose_chart_columns(
        pd.DataFrame(
            {"order_status": ["delivered", "shipped"], "total_orders": [10, 4]}
        )
    )

    assert chart_columns == ("order_status", "total_orders")
    assert app._choose_chart_columns(
        pd.DataFrame({"order_id": [1, 2], "customer_id": [11, 12]})
    ) is None


def test_unexpected_error_does_not_leak_implementation_details() -> None:
    message = app._user_facing_error(RuntimeError("C:/private/path/api-token"))

    assert "C:/private" not in message
    assert "Không thể hoàn tất" in message


def test_missing_key_does_not_substitute_benchmark_reference_sql(monkeypatch) -> None:
    """Normal questions must reach Gemini rather than borrowing benchmark SQL."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(text_to_sql, "get_schema", lambda _db_path: "orders: order_id")
    generated: list[dict[str, object]] = []

    def generate_fresh_sql(**kwargs: object) -> str:
        generated.append(kwargs)
        return "SELECT 1 AS generated_query"

    monkeypatch.setattr(text_to_sql, "generate_sql", generate_fresh_sql)
    monkeypatch.setattr(
        sql_validator,
        "validate_sql",
        lambda _sql, **_kwargs: ValidationResult(
            valid=False, errors=["stop before execution"]
        ),
    )

    outcome = app.answer_question("Tổng số đơn hàng là bao nhiêu?", use_rag=False)

    assert generated
    assert outcome.sql == "SELECT 1 AS generated_query"
    assert outcome.is_demo is False


def test_explicit_demo_sql_is_labeled_as_demo(monkeypatch) -> None:
    """Demo mode is allowed only when a caller supplies its SQL explicitly."""
    monkeypatch.setattr(text_to_sql, "get_schema", lambda _db_path: "orders: order_id")
    monkeypatch.setattr(
        text_to_sql,
        "generate_sql",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Gemini was called")),
    )
    monkeypatch.setattr(
        sql_validator,
        "validate_sql",
        lambda _sql, **_kwargs: ValidationResult(
            valid=False, errors=["stop before execution"]
        ),
    )

    outcome = app.answer_question(
        "Demo only", use_rag=False, force_demo_sql="SELECT 1 AS demo_value"
    )

    assert outcome.sql == "SELECT 1 AS demo_value"
    assert outcome.is_demo is True


@pytest.mark.requires_olist_database
def test_invalid_generated_sql_is_never_executed(monkeypatch) -> None:
    monkeypatch.setattr(text_to_sql, "get_schema", lambda _db_path: "orders: order_id")
    monkeypatch.setattr(
        text_to_sql,
        "generate_sql",
        lambda **_kwargs: "SELECT revenue FROM orders",
    )

    def execution_must_not_happen(*_args, **_kwargs):
        raise AssertionError("Invalid SQL reached the executor")

    monkeypatch.setattr(app, "execute_read_only_query", execution_must_not_happen)

    outcome = app.answer_question("Doanh thu là bao nhiêu?", use_rag=False)

    assert outcome.valid is False
    assert outcome.dataframe is None
    assert any("Cột không tồn tại" in error for error in outcome.errors or [])
