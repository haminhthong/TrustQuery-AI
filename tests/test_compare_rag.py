"""Tests for the safe, bounded bilingual RAG benchmark evaluator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from src.evaluation import compare_rag
from src.validation.sql_validator import ValidationResult


class _Cursor:
    description = [("value",)]

    def __init__(self, rows: list[tuple[int, ...]], fetch_sizes: list[int]) -> None:
        self._rows = rows
        self._fetch_sizes = fetch_sizes

    def fetchmany(self, size: int) -> list[tuple[int, ...]]:
        self._fetch_sizes.append(size)
        return self._rows[:size]


class _Connection:
    def __init__(
        self,
        executed_sql: list[str],
        fetch_sizes: list[int],
        rows_for_sql: Callable[[str], list[tuple[int, ...]]] | None = None,
    ) -> None:
        self.executed_sql = executed_sql
        self.fetch_sizes = fetch_sizes
        self.rows_for_sql = rows_for_sql or (lambda _sql: [(1,)])

    def execute(self, sql: str) -> _Cursor:
        self.executed_sql.append(sql)
        return _Cursor(self.rows_for_sql(sql), self.fetch_sizes)

    def close(self) -> None:
        pass


def _valid_sql(sql: str, *, db_path: Path) -> ValidationResult:
    del db_path
    return ValidationResult(valid=True, sql=sql)


def _query_record(**overrides: str) -> dict[str, str]:
    record = {
        "id": "Q001",
        "question_vi": "Tổng số đơn?",
        "question_en": "How many orders?",
        "difficulty": "easy",
        "category": "basic_sql",
        "split": "development",
        "expected_outcome": "query",
        "reference_sql": "SELECT 1 AS reference",
    }
    record.update(overrides)
    return record


def _clarify_record(**overrides: str) -> dict[str, str]:
    record = {
        "id": "Q120",
        "question_vi": "Cần làm rõ?",
        "question_en": "Needs clarification?",
        "difficulty": "hard",
        "category": "ambiguous_out_of_scope",
        "split": "test",
        "expected_outcome": "clarify_or_refuse",
        "evaluation_note": "Ask a follow-up or explain the data limitation.",
    }
    record.update(overrides)
    return record


def test_compare_uses_db_path_caches_reference_and_labels_validation_gate(
    monkeypatch: Any,
) -> None:
    generated: list[tuple[str, str | None]] = []
    executed_sql: list[str] = []
    fetch_sizes: list[int] = []
    schema_paths: list[Path] = []
    db_path = Path("benchmark.duckdb")

    def get_schema(path: Path) -> str:
        schema_paths.append(path)
        return "orders: order_id"

    def generate(question: str, _schema: str, context: str | None = None) -> str:
        generated.append((question, context))
        return "SELECT 1 AS candidate"

    monkeypatch.setattr(compare_rag, "get_schema", get_schema)
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
        lambda _db_path: _Connection(executed_sql, fetch_sizes),
    )

    report = compare_rag.compare_questions([_query_record()], db_path=db_path)
    results = report["results"]

    assert [result["language"] for result in results] == ["vi", "en"]
    assert all(result["schema_only"]["matches_reference"] is True for result in results)
    assert all(result["rag"]["matches_reference"] is True for result in results)
    assert all(
        result["rag_validation_gate"]["is_independent_llm_method"] is False
        for result in results
    )
    assert all("rag_validation" not in result for result in results)
    assert len(generated) == 4
    assert executed_sql.count("SELECT 1 AS reference") == 1
    assert len(executed_sql) == 5
    assert schema_paths == [db_path]
    assert fetch_sizes == [compare_rag.MAX_RESULT_ROWS + 1] * 5
    assert set(report["summary"]["methods"]) == {"schema_only", "rag"}
    assert report["summary"]["validation_gate"]["is_independent_llm_method"] is False
    assert report["summary"]["validation_gate"]["completed_comparison_count"] == 2


def test_clarify_cases_make_no_generation_retrieval_validation_or_execution_calls(
    monkeypatch: Any,
) -> None:
    generated: list[str] = []
    retrieved: list[str] = []
    executed_sql: list[str] = []
    fetch_sizes: list[int] = []
    monkeypatch.setattr(compare_rag, "get_schema", lambda _path: "orders: order_id")

    def generate(question: str, _schema: str, context: str | None = None) -> str:
        del context
        generated.append(question)
        return "DROP TABLE orders" if question.startswith("Unsafe") else "SELECT 1"

    def retrieve(question: str, *, model_name: str) -> str:
        del model_name
        retrieved.append(question)
        return "rules"

    def validate(sql: str, *, db_path: Path) -> ValidationResult:
        del db_path
        return ValidationResult(valid=not sql.startswith("DROP"), sql=sql)

    monkeypatch.setattr(compare_rag, "generate_sql", generate)
    monkeypatch.setattr(compare_rag, "retrieve_context", retrieve)
    monkeypatch.setattr(compare_rag, "validate_sql", validate)
    monkeypatch.setattr(
        compare_rag,
        "connect_read_only",
        lambda _db_path: _Connection(executed_sql, fetch_sizes),
    )

    report = compare_rag.compare_questions(
        [
            _query_record(question_vi="Unsafe question", question_en="Unsafe English"),
            _clarify_record(),
        ],
        db_path=Path("benchmark.duckdb"),
    )

    query_cases = report["results"][:2]
    refusal_cases = report["results"][2:]
    assert all(case["schema_only"]["validation"]["valid"] is False for case in query_cases)
    assert executed_sql == ["SELECT 1 AS reference"]
    assert generated == ["Unsafe question", "Unsafe question", "Unsafe English", "Unsafe English"]
    assert retrieved == ["Unsafe question", "Unsafe English"]
    assert all(case["manual_review_required"] is True for case in refusal_cases)
    assert all("schema_only" not in case and "rag" not in case for case in refusal_cases)
    assert report["summary"]["clarify_or_refuse_case_count"] == 2
    assert report["summary"]["query_case_count"] == 2


def test_clarify_only_cases_do_not_load_schema(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        compare_rag,
        "get_schema",
        lambda _path: pytest.fail("schema must not load for manual-review cases"),
    )
    report = compare_rag.compare_questions([_clarify_record()])

    assert len(report["results"]) == 2
    assert report["summary"]["query_case_count"] == 0
    assert report["summary"]["clarify_or_refuse_case_count"] == 2


def test_evaluator_failures_are_sanitized_in_report(monkeypatch: Any) -> None:
    secret = "API_KEY=do-not-leak:C:\\private\\benchmark.duckdb"
    monkeypatch.setattr(compare_rag, "get_schema", lambda _path: "orders: order_id")
    monkeypatch.setattr(
        compare_rag,
        "generate_sql",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    monkeypatch.setattr(
        compare_rag,
        "retrieve_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    monkeypatch.setattr(
        compare_rag,
        "validate_sql",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    report = compare_rag.compare_questions([_query_record()])
    result = report["results"][0]
    assert secret not in json.dumps(report, ensure_ascii=False)
    assert result["schema_only"]["error_code"] == "generation_failed"
    assert result["rag"]["retrieval_error_code"] == "retrieval_failed"
    assert result["reference"]["validation"]["errors"] == [
        "Không thể xác thực SQL do lỗi nội bộ của evaluator."
    ]


def test_contract_is_strict_before_schema_load(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(
        compare_rag,
        "get_schema",
        lambda _path: pytest.fail("schema must not be loaded"),
    )
    malformed = _query_record()
    malformed.pop("question_en")
    with pytest.raises(ValueError, match="question_en"):
        compare_rag.compare_questions([malformed])

    clarify_with_sql = _clarify_record()
    clarify_with_sql["reference_sql"] = "SELECT 1"
    with pytest.raises(ValueError, match="reference_sql"):
        compare_rag.validate_benchmark_contract([clarify_with_sql])

    path = tmp_path / "malformed.json"
    path.write_text(json.dumps([_query_record(), "not an object"]), encoding="utf-8")
    with pytest.raises(ValueError, match="đối tượng JSON"):
        compare_rag.load_questions(path)


def test_row_limit_marks_candidate_non_comparable(monkeypatch: Any) -> None:
    executed_sql: list[str] = []
    fetch_sizes: list[int] = []
    monkeypatch.setattr(compare_rag, "MAX_RESULT_ROWS", 1)
    monkeypatch.setattr(compare_rag, "get_schema", lambda _path: "orders: order_id")
    monkeypatch.setattr(compare_rag, "generate_sql", lambda *_args, **_kwargs: "SELECT 1")
    monkeypatch.setattr(compare_rag, "retrieve_context", lambda *_args, **_kwargs: "rules")
    monkeypatch.setattr(compare_rag, "validate_sql", _valid_sql)
    monkeypatch.setattr(
        compare_rag,
        "connect_read_only",
        lambda _db_path: _Connection(
            executed_sql,
            fetch_sizes,
            lambda sql: [(1,)] if "reference" in sql else [(1,), (2,)],
        ),
    )

    report = compare_rag.compare_questions([_query_record()])
    candidate = report["results"][0]["schema_only"]
    assert candidate["execution"] == {
        "status": "not_comparable",
        "reason": "result_row_limit_exceeded",
        "row_limit": 1,
        "comparable": False,
    }
    assert "matches_reference" not in candidate
    assert fetch_sizes == [2] * 5
    assert report["summary"]["methods"]["schema_only"]["execution_accuracy"] == 0.0
    assert report["summary"]["validation_gate"]["non_comparable_count"] == 2


def test_validator_requires_normalized_sql_before_execution(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        compare_rag,
        "validate_sql",
        lambda *_args, **_kwargs: ValidationResult(valid=True, sql=None),
    )
    payload, safe_sql = compare_rag._validation_payload("SELECT 1", Path("db.duckdb"))
    assert payload["valid"] is False
    assert safe_sql is None


def test_question_ids_override_default_split_filter() -> None:
    questions = [
        {"id": "Q001", "split": "development"},
        {"id": "Q101", "split": "test"},
    ]
    assert [
        item["id"]
        for item in compare_rag._select_questions(
            questions, split="development", question_ids=None
        )
    ] == ["Q001"]
    assert [
        item["id"]
        for item in compare_rag._select_questions(
            questions, split="development", question_ids=["Q101"]
        )
    ] == ["Q101"]
    assert len(compare_rag._select_questions(questions, split="all", question_ids=None)) == 2
