"""Tests for safe, bilingual RAG benchmark orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.evaluation import compare_rag
from src.validation.sql_validator import ValidationResult


class _Cursor:
    description = [("value",)]

    def __init__(self, sql: str) -> None:
        self.sql = sql

    def fetchall(self) -> list[tuple[int]]:
        return [(1,)]


class _Connection:
    def __init__(self, executed_sql: list[str]) -> None:
        self.executed_sql = executed_sql
        self.closed = False

    def execute(self, sql: str) -> _Cursor:
        self.executed_sql.append(sql)
        return _Cursor(sql)

    def close(self) -> None:
        self.closed = True


def _valid_sql(sql: str, *, db_path: Path) -> ValidationResult:
    del db_path
    return ValidationResult(valid=True, sql=sql)


def test_compare_questions_emits_bilingual_cases_and_reuses_rag_candidate(
    monkeypatch: Any,
) -> None:
    generated: list[tuple[str, str | None]] = []
    executed_sql: list[str] = []
    db_path = Path("benchmark.duckdb")

    monkeypatch.setattr(compare_rag, "get_schema", lambda: "orders: order_id")

    def generate(question: str, _schema: str, context: str | None = None) -> str:
        generated.append((question, context))
        return "SELECT 1 AS candidate"

    monkeypatch.setattr(compare_rag, "generate_sql", generate)
    monkeypatch.setattr(
        compare_rag,
        "retrieve_context",
        lambda question, *, model_name: f"rules for {question} ({model_name})",
    )
    monkeypatch.setattr(compare_rag, "validate_sql", _valid_sql)
    monkeypatch.setattr(
        compare_rag,
        "connect_read_only",
        lambda _db_path: _Connection(executed_sql),
    )

    report = compare_rag.compare_questions(
        [
            {
                "id": "Q001",
                "question_vi": "Tổng số đơn?",
                "question_en": "How many orders?",
                "difficulty": "easy",
                "category": "basic_sql",
                "split": "development",
                "expected_outcome": "query",
                "reference_sql": "SELECT 1 AS reference",
            }
        ],
        db_path=db_path,
    )

    results = report["results"]
    assert [result["language"] for result in results] == ["vi", "en"]
    assert [result["case_id"] for result in results] == ["Q001_vi", "Q001_en"]
    assert all(result["category"] == "basic_sql" for result in results)
    assert all(result["split"] == "development" for result in results)
    assert all(result["expected_outcome"] == "query" for result in results)
    assert all(result["reference_sql"] == "SELECT 1 AS reference" for result in results)
    assert all(result["schema_only"]["matches_reference"] is True for result in results)
    assert all(result["rag"]["matches_reference"] is True for result in results)
    assert all(result["rag_validation"]["candidate_reused"] is True for result in results)
    assert all(result["rag_validation"]["sql"] == result["rag"]["sql"] for result in results)

    # Two language cases x (schema-only + RAG) generation calls. A third RAG+
    # Validation LLM call would make this six instead of four.
    assert len(generated) == 4
    assert [context for _, context in generated].count(None) == 2
    assert report["summary"]["methods"]["rag_validation"]["execution_accuracy"] == 1.0
    assert report["summary"]["methods"]["rag_validation"]["valid_sql_rate"] == 1.0
    # Each language has one validated reference and two validated candidates.
    assert len(executed_sql) == 6


def test_invalid_sql_is_never_executed_and_refusal_cases_need_manual_review(
    monkeypatch: Any,
) -> None:
    executed_sql: list[str] = []
    db_path = Path("benchmark.duckdb")
    monkeypatch.setattr(compare_rag, "get_schema", lambda: "orders: order_id")

    def generate(question: str, _schema: str, context: str | None = None) -> str:
        del context
        if question == "Unsafe question":
            return "DROP TABLE orders"
        return "SELECT 1 AS candidate"

    def validate(sql: str, *, db_path: Path) -> ValidationResult:
        del db_path
        if sql.startswith("DROP"):
            return ValidationResult(valid=False, errors=["mutation blocked"])
        return ValidationResult(valid=True, sql=sql)

    monkeypatch.setattr(compare_rag, "generate_sql", generate)
    monkeypatch.setattr(compare_rag, "retrieve_context", lambda *_args, **_kwargs: "rules")
    monkeypatch.setattr(compare_rag, "validate_sql", validate)
    monkeypatch.setattr(
        compare_rag,
        "connect_read_only",
        lambda _db_path: _Connection(executed_sql),
    )

    report = compare_rag.compare_questions(
        [
            {
                "id": "Q001",
                "question_vi": "Unsafe question",
                "expected_outcome": "query",
                "reference_sql": "SELECT 1 AS reference",
            },
            {
                "id": "Q120",
                "question_vi": "Need clarification",
                "expected_outcome": "clarify_or_refuse",
                "category": "ambiguous_out_of_scope",
                "split": "test",
                "evaluation_note": "Ask a follow-up or explain the data limitation.",
            },
        ],
        db_path=db_path,
    )

    query_case, refusal_case = report["results"]
    assert query_case["schema_only"]["validation"]["valid"] is False
    assert query_case["schema_only"]["execution"] == {
        "status": "skipped",
        "reason": "validation_failed",
    }
    assert query_case["rag_validation"]["execution"] == {
        "status": "skipped",
        "reason": "validation_failed",
    }
    # Only the trusted reference passed validation; no DROP candidate reached
    # the read-only connection.
    assert executed_sql == ["SELECT 1 AS reference"]

    assert "reference_sql" not in refusal_case
    assert "reference" not in refusal_case
    assert refusal_case["manual_review_required"] is True
    assert refusal_case["schema_only"]["execution"]["status"] == "not_applicable"
    assert refusal_case["rag"]["execution"]["status"] == "not_applicable"
    assert refusal_case["rag_validation"]["candidate_reused"] is True
    assert report["summary"]["clarify_or_refuse_case_count"] == 1
    assert report["summary"]["methods"]["schema_only"]["false_acceptance_rate"] == 0.0


def test_compare_questions_keeps_schema_only_result_when_retrieval_fails(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(compare_rag, "get_schema", lambda: "orders: order_id")
    monkeypatch.setattr(
        compare_rag,
        "generate_sql",
        lambda question, schema, context=None: "SELECT 1 AS candidate",
    )
    monkeypatch.setattr(
        compare_rag,
        "validate_sql",
        lambda sql, *, db_path: ValidationResult(
            valid=False, sql=sql, errors=["database unavailable"]
        ),
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
    assert result["rag_validation"]["candidate_reused"] is True
