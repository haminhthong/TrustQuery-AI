"""Safety-focused tests for the Streamlit application's query pipeline."""

from __future__ import annotations

import app
import pandas as pd
import pytest
import src.llm.text_to_sql as text_to_sql


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
