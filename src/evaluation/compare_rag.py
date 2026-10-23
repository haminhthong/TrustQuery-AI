"""Generate a safe, execution-based Text-to-SQL benchmark comparison.

For each benchmark record this module evaluates its Vietnamese and English
questions independently. It generates one schema-only candidate and one RAG
candidate per language, then applies the full SQL validator before a candidate
can reach DuckDB. The ``rag_validation`` view deliberately reuses the RAG
candidate instead of asking the LLM for a third query.

Only ``expected_outcome == \"query\"`` cases have a reference SQL statement and
are eligible for execution-output comparison. Ambiguous or out-of-scope
``clarify_or_refuse`` cases are retained in the report as manual-review cases;
they never acquire an invented reference query or execute SQL.

Run after building the local RAG index and configuring ``.env``:

    python -m src.evaluation.compare_rag
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from src.console import configure_utf8_output
from src.database.read_only import connect_read_only
from src.llm.text_to_sql import generate_sql, get_schema
from src.rag.build_index import EMBEDDING_MODEL
from src.rag.retriever import retrieve_context
from src.validation.sql_safety import UnsafeSQLError
from src.validation.sql_validator import DB_PATH, validate_sql


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = ROOT / "benchmarks" / "questions.json"
DEFAULT_OUTPUT_PATH = ROOT / "benchmarks" / "results" / "rag_comparison.json"

QUERY_OUTCOME = "query"
CLARIFY_OR_REFUSE_OUTCOME = "clarify_or_refuse"
LANGUAGE_FIELDS = (("vi", "question_vi"), ("en", "question_en"))
METHOD_NAMES = ("schema_only", "rag", "rag_validation")


def load_questions(path: Path = BENCHMARK_PATH) -> list[dict[str, Any]]:
    """Read the shared benchmark used for all experimental variants."""
    try:
        content = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Không đọc được bộ benchmark: {path}") from error
    if not isinstance(content, list):
        raise ValueError("Bộ benchmark phải là một mảng JSON.")
    return [item for item in content if isinstance(item, dict)]


def _question_cases(questions: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    """Yield one independent evaluation case for every available language.

    ``id`` remains the stable business-question identifier while ``case_id``
    makes the language variant unambiguous in a flat report. Older benchmark
    rows that predate the expanded contract default to a normal query case,
    which keeps the eight original fixtures useful during migration.
    """
    for item in questions:
        benchmark_id = item.get("id")
        expected_outcome = str(item.get("expected_outcome") or QUERY_OUTCOME).strip()
        for language, field in LANGUAGE_FIELDS:
            question = item.get(field)
            if not isinstance(question, str) or not question.strip():
                continue

            case: dict[str, Any] = {
                "id": benchmark_id,
                "case_id": f"{benchmark_id}_{language}",
                "language": language,
                "question_language": language,
                "question": question.strip(),
                "difficulty": item.get("difficulty"),
                "category": item.get("category"),
                "split": item.get("split"),
                "expected_outcome": expected_outcome,
            }
            if "evaluation_note" in item:
                case["evaluation_note"] = item.get("evaluation_note")
            # A query case without a reference is reported as malformed rather
            # than borrowing a reference from another record. In particular,
            # clarify_or_refuse records intentionally have no reference_sql.
            if expected_outcome == QUERY_OUTCOME:
                case["reference_sql"] = item.get("reference_sql")
            yield case


def _candidate_or_error(
    question: str, schema: str, context: str | None = None
) -> dict[str, Any]:
    """Keep one failing LLM generation from stopping the whole benchmark."""
    try:
        return {"sql": generate_sql(question, schema, context=context)}
    except (RuntimeError, UnsafeSQLError, ValueError) as error:
        return {"error": str(error)}


def _validation_payload(sql: str, db_path: Path) -> tuple[dict[str, Any], str | None]:
    """Validate SQL and return only normalized SQL that passed the validator."""
    try:
        result = validate_sql(sql, db_path=db_path)
    except Exception as error:  # Keep the batch report usable if local setup fails.
        return (
            {
                "valid": False,
                "sql": None,
                "errors": [f"Không thể xác thực SQL: {error}"],
                "warnings": [],
            },
            None,
        )

    valid = bool(getattr(result, "valid", False))
    normalized_sql = getattr(result, "sql", None)
    if not isinstance(normalized_sql, str) or not normalized_sql.strip():
        normalized_sql = None
    payload = {
        "valid": valid,
        "sql": normalized_sql,
        "errors": list(getattr(result, "errors", []) or []),
        "warnings": list(getattr(result, "warnings", []) or []),
    }
    # A validator result without normalized SQL is never an executable approval.
    return payload, normalized_sql if valid else None


def _comparison_value(value: Any) -> Any:
    """Convert DuckDB values to stable, type-aware JSON-compatible values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": format(value, "f")}
    if isinstance(value, float):
        if math.isnan(value):
            return {"type": "float", "value": "nan"}
        if math.isinf(value):
            return {"type": "float", "value": "infinity" if value > 0 else "-infinity"}
        return {"type": "float", "value": repr(value)}
    if isinstance(value, (datetime, date, time)):
        return {"type": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, (list, tuple)):
        return [_comparison_value(member) for member in value]
    if isinstance(value, dict):
        return {
            str(key): _comparison_value(member)
            for key, member in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    return {"type": type(value).__name__, "value": str(value)}


def _row_fingerprints(rows: Iterable[tuple[Any, ...]]) -> Counter[str]:
    """Build an order-insensitive multiset of query-output rows."""
    return Counter(
        json.dumps(
            [_comparison_value(value) for value in row],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for row in rows
    )


def _result_digest(fingerprints: Counter[str]) -> str:
    """Return a compact audit trail without putting full result data in JSON."""
    canonical = json.dumps(
        sorted(fingerprints.items()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _execute_validated_sql(
    validated_sql: str | None, db_path: Path
) -> tuple[dict[str, Any], Counter[str] | None]:
    """Execute only SQL that has already received a positive validator result."""
    if not isinstance(validated_sql, str) or not validated_sql.strip():
        return (
            {"status": "skipped", "reason": "validation_failed"},
            None,
        )

    try:
        connection = connect_read_only(db_path)
        try:
            cursor = connection.execute(validated_sql)
            rows = cursor.fetchall()
            description = getattr(cursor, "description", None) or []
        finally:
            connection.close()
    except Exception as error:
        return ({"status": "error", "error": str(error)}, None)

    fingerprints = _row_fingerprints(rows)
    columns = [str(column[0]) for column in description if column]
    return (
        {
            "status": "completed",
            "row_count": len(rows),
            "columns": columns,
            "result_digest": _result_digest(fingerprints),
        },
        fingerprints,
    )


def _reference_assessment(case: dict[str, Any], db_path: Path) -> dict[str, Any]:
    """Validate and execute a query-case reference without trusting it blindly."""
    reference_sql = case.get("reference_sql")
    if not isinstance(reference_sql, str) or not reference_sql.strip():
        return {
            "validation": {
                "valid": False,
                "sql": None,
                "errors": ["Query benchmark thiếu reference_sql."],
                "warnings": [],
            },
            "execution": {"status": "skipped", "reason": "missing_reference_sql"},
            "_fingerprints": None,
        }

    validation, safe_sql = _validation_payload(reference_sql, db_path)
    execution, fingerprints = _execute_validated_sql(safe_sql, db_path)
    return {
        "sql": reference_sql,
        "validation": validation,
        "execution": execution,
        "_fingerprints": fingerprints,
    }


def _assess_candidate(
    candidate: dict[str, Any],
    *,
    expected_outcome: str,
    reference: dict[str, Any] | None,
    db_path: Path,
) -> dict[str, Any]:
    """Attach validation and, only when safe and comparable, execution data."""
    assessed = dict(candidate)
    sql = assessed.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        assessed["validation"] = {
            "valid": False,
            "sql": None,
            "errors": [],
            "warnings": [],
        }
        assessed["execution"] = {
            "status": "not_applicable"
            if expected_outcome == CLARIFY_OR_REFUSE_OUTCOME
            else "skipped",
            "reason": "candidate_not_generated"
            if expected_outcome != CLARIFY_OR_REFUSE_OUTCOME
            else "clarify_or_refuse_requires_manual_review",
        }
        return assessed

    validation, safe_sql = _validation_payload(sql, db_path)
    assessed["validation"] = validation
    if expected_outcome == CLARIFY_OR_REFUSE_OUTCOME:
        assessed["execution"] = {
            "status": "not_applicable",
            "reason": "clarify_or_refuse_requires_manual_review",
        }
        assessed["manual_review_required"] = True
        return assessed

    if safe_sql is None:
        assessed["execution"] = {"status": "skipped", "reason": "validation_failed"}
        return assessed
    if reference is None:
        assessed["execution"] = {"status": "skipped", "reason": "missing_reference"}
        return assessed

    reference_execution = reference.get("execution", {})
    reference_fingerprints = reference.get("_fingerprints")
    if reference_execution.get("status") != "completed" or reference_fingerprints is None:
        assessed["execution"] = {
            "status": "skipped",
            "reason": "reference_not_executable",
        }
        return assessed

    execution, fingerprints = _execute_validated_sql(safe_sql, db_path)
    assessed["execution"] = execution
    if execution.get("status") == "completed" and fingerprints is not None:
        assessed["matches_reference"] = fingerprints == reference_fingerprints
    return assessed


def _public_reference(reference: dict[str, Any]) -> dict[str, Any]:
    """Drop internal comparison data before serializing the report."""
    return {key: value for key, value in reference.items() if key != "_fingerprints"}


def _rag_validation_view(rag: dict[str, Any]) -> dict[str, Any]:
    """Expose the validation-gated RAG variant without another LLM call."""
    view = copy.deepcopy(rag)
    view["candidate_source"] = "rag"
    view["candidate_reused"] = True
    return view


def _empty_method_summary() -> dict[str, Any]:
    return {
        "query_case_count": 0,
        "candidate_sql_count": 0,
        "valid_sql_count": 0,
        "executed_case_count": 0,
        "execution_match_count": 0,
        "false_acceptance_count": 0,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate transparent metrics without pretending to score refusals."""
    method_summaries = {method: _empty_method_summary() for method in METHOD_NAMES}
    query_case_count = 0
    manual_review_cases = 0

    for result in results:
        expected_outcome = result.get("expected_outcome")
        if expected_outcome == CLARIFY_OR_REFUSE_OUTCOME:
            manual_review_cases += 1
            continue
        if expected_outcome != QUERY_OUTCOME:
            # Unknown outcome labels cannot be meaningfully compared to SQL.
            manual_review_cases += 1
            continue

        query_case_count += 1
        for method, metrics in method_summaries.items():
            metrics["query_case_count"] += 1
            candidate = result.get(method, {})
            if not isinstance(candidate, dict):
                continue
            if isinstance(candidate.get("sql"), str) and candidate["sql"].strip():
                metrics["candidate_sql_count"] += 1
            validation = candidate.get("validation")
            if not isinstance(validation, dict) or not validation.get("valid"):
                continue
            metrics["valid_sql_count"] += 1
            execution = candidate.get("execution")
            if not isinstance(execution, dict) or execution.get("status") != "completed":
                continue
            metrics["executed_case_count"] += 1
            if candidate.get("matches_reference") is True:
                metrics["execution_match_count"] += 1
            else:
                metrics["false_acceptance_count"] += 1

    for metrics in method_summaries.values():
        total = metrics["query_case_count"]
        valid = metrics["valid_sql_count"]
        metrics["valid_sql_rate"] = _ratio(valid, total)
        # Failed validation, execution errors, and unavailable references count
        # as misses: that is the conservative benchmark accuracy.
        metrics["execution_accuracy"] = _ratio(
            metrics["execution_match_count"], total
        )
        metrics["false_acceptance_rate"] = _ratio(
            metrics["false_acceptance_count"], total
        )
        metrics["false_acceptance_among_valid_rate"] = _ratio(
            metrics["false_acceptance_count"], valid
        )

    clarify_case_count = sum(
        1
        for result in results
        if result.get("expected_outcome") == CLARIFY_OR_REFUSE_OUTCOME
    )
    return {
        "total_cases": len(results),
        "query_case_count": query_case_count,
        "clarify_or_refuse_case_count": clarify_case_count,
        "manual_review_required_case_count": manual_review_cases,
        "metric_definitions": {
            "execution_accuracy": (
                "Execution outputs matching reference SQL divided by all query cases; "
                "invalid, failed, or unexecuted candidates count as misses."
            ),
            "valid_sql_rate": "Validator-approved candidates divided by all query cases.",
            "false_acceptance_rate": (
                "Validator-approved candidates with a non-matching execution output "
                "divided by all query cases."
            ),
        },
        "methods": method_summaries,
        "clarify_or_refuse": {
            "case_count": clarify_case_count,
            "manual_review_required": True,
            "scored_automatically": False,
        },
    }


def compare_questions(
    questions: list[dict[str, Any]],
    *,
    model_name: str = EMBEDDING_MODEL,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Compare schema-only, RAG, and RAG+Validation on bilingual cases.

    Every generated candidate goes through ``validate_sql``. The only
    execution helper accepts normalized SQL returned by a successful validator
    call and uses DuckDB's local read-only connection with external access
    disabled.
    """
    db_path = Path(db_path)
    # Keep the schema-generation API independent from evaluation execution.
    # ``get_schema`` already reads the project's local database read-only;
    # ``db_path`` is supplied below to validation/execution for testability.
    schema = get_schema()
    results: list[dict[str, Any]] = []
    for case in _question_cases(questions):
        expected_outcome = case["expected_outcome"]
        reference: dict[str, Any] | None = None
        if expected_outcome == QUERY_OUTCOME:
            reference = _reference_assessment(case, db_path)

        schema_only = _assess_candidate(
            _candidate_or_error(case["question"], schema),
            expected_outcome=expected_outcome,
            reference=reference,
            db_path=db_path,
        )

        try:
            context = retrieve_context(case["question"], model_name=model_name)
        except (RuntimeError, ValueError) as error:
            rag = {
                "retrieval_error": str(error),
                "validation": {
                    "valid": False,
                    "sql": None,
                    "errors": [],
                    "warnings": [],
                },
                "execution": {
                    "status": "not_applicable"
                    if expected_outcome == CLARIFY_OR_REFUSE_OUTCOME
                    else "skipped",
                    "reason": "retrieval_failed",
                },
            }
        else:
            rag = _assess_candidate(
                {
                    "context": context,
                    **_candidate_or_error(case["question"], schema, context=context),
                },
                expected_outcome=expected_outcome,
                reference=reference,
                db_path=db_path,
            )

        result = {
            **case,
            "schema_only": schema_only,
            "rag": rag,
            # This is a view over ``rag``, not a fresh model response.
            "rag_validation": _rag_validation_view(rag),
        }
        if reference is not None:
            result["reference"] = _public_reference(reference)
        else:
            result["manual_review_required"] = True
        results.append(result)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Execution-based comparison of schema-only Text-to-SQL, RAG, and "
            "RAG+Validation. Only validator-approved SQL is executed locally."
        ),
        "results": results,
        "summary": _build_summary(results),
    }


def main() -> None:
    """Run the shared benchmark and save auditable comparison data."""
    configure_utf8_output()
    parser = argparse.ArgumentParser(
        description="So sánh Text-to-SQL schema-only, RAG và RAG+Validation"
    )
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--rag-model",
        default=EMBEDDING_MODEL,
        help="Model embedding RAG; phải khớp model đã dùng khi lập chỉ mục",
    )
    parser.add_argument(
        "--question-id",
        action="append",
        dest="question_ids",
        help="Chỉ chạy mã benchmark này; có thể truyền nhiều lần",
    )
    arguments = parser.parse_args()
    questions = load_questions(arguments.benchmark)
    if arguments.question_ids:
        selected_ids = set(arguments.question_ids)
        questions = [item for item in questions if item.get("id") in selected_ids]
    if not questions:
        raise ValueError("Không có câu hỏi benchmark nào được chọn.")

    report = compare_questions(questions, model_name=arguments.rag_model)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = report["summary"]
    print(
        f"Đã tạo báo cáo cho {summary['total_cases']} ca ngôn ngữ: {arguments.output}"
    )
    for method, metrics in summary["methods"].items():
        print(
            f"{method}: execution_accuracy={metrics['execution_accuracy']}, "
            f"valid_sql_rate={metrics['valid_sql_rate']}, "
            f"false_acceptance_rate={metrics['false_acceptance_rate']}"
        )
    if summary["clarify_or_refuse_case_count"]:
        print(
            "Các ca clarify_or_refuse được giữ để đánh giá thủ công; "
            "không thực thi hoặc ép reference_sql."
        )


if __name__ == "__main__":
    main()
