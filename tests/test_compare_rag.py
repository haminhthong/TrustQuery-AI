"""Tests for fault-tolerant A/B comparison orchestration."""

from __future__ import annotations

from typing import Any

from src.evaluation import compare_rag


def test_compare_questions_keeps_schema_only_result_when_retrieval_fails(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(compare_rag, "get_schema", lambda: "orders: order_id")
    monkeypatch.setattr(
        compare_rag,
        "generate_sql",
        lambda question, schema, context=None: "SELECT 1 AS candidate",
    )

    def fail_retrieval(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(compare_rag, "retrieve_context", fail_retrieval)

    report = compare_rag.compare_questions(
        [{"id": "Q001", "question_vi": "Tổng số đơn?", "reference_sql": "SELECT 1"}]
    )

    result = report["results"][0]
    assert result["schema_only"]["sql"] == "SELECT 1 AS candidate"
    assert result["rag"]["retrieval_error"] == "index unavailable"
