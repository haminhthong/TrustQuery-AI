"""Generate a safe, execution-based Text-to-SQL benchmark comparison.

For each query benchmark record this module evaluates its Vietnamese and
English questions independently. It generates one schema-only candidate and
one RAG candidate per language, then applies the full SQL validator before a
candidate can reach DuckDB. The RAG validation-gate view deliberately reuses
the RAG candidate; it is not a third LLM method.

Only ``expected_outcome == \"query\"`` cases have a reference SQL statement and
are eligible for execution-output comparison. Ambiguous or out-of-scope
``clarify_or_refuse`` cases are retained in the report as manual-review cases.
They do not trigger retrieval, generation, validation, or execution.

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
from src.validation.sql_validator import DB_PATH, validate_sql


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = ROOT / "benchmarks" / "questions.json"
DEFAULT_OUTPUT_PATH = ROOT / "benchmarks" / "results" / "rag_comparison.json"

QUERY_OUTCOME = "query"
CLARIFY_OR_REFUSE_OUTCOME = "clarify_or_refuse"
LANGUAGE_FIELDS = (("vi", "question_vi"), ("en", "question_en"))
METHOD_NAMES = ("schema_only", "rag")
SUPPORTED_SPLITS = frozenset({"development", "test"})
SUPPORTED_OUTCOMES = frozenset({QUERY_OUTCOME, CLARIFY_OR_REFUSE_OUTCOME})
SUPPORTED_DIFFICULTIES = frozenset({"easy", "medium", "hard"})
SUPPORTED_CATEGORIES = frozenset(
    {
        "basic_sql",
        "filters_time",
        "joins",
        "aggregation",
        "complex_business",
        "ambiguous_out_of_scope",
    }
)
BENCHMARK_FIELDS = frozenset(
    {
        "id",
        "question_vi",
        "question_en",
        "difficulty",
        "category",
        "split",
        "expected_outcome",
        "reference_sql",
        "evaluation_note",
    }
)
# ``fetchmany`` requests one additional row to determine whether a result can
# be compared without materialising an unbounded result set.
MAX_RESULT_ROWS = 10_000


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _contract_error(index: int, message: str) -> ValueError:
    return ValueError(f"Bản ghi benchmark ở vị trí {index}: {message}")


def validate_benchmark_contract(questions: Any) -> list[dict[str, Any]]:
    """Validate the complete benchmark before any model or database work.

    The evaluator intentionally has no migration defaults: silently treating a
    malformed row as a query case can cause reference SQL to be invented or a
    holdout to be skipped.  Extra fields are rejected so misspelled contract
    fields cannot silently disappear from the report.
    """
    if not isinstance(questions, list) or not questions:
        raise ValueError("Bộ benchmark phải là một mảng JSON không rỗng.")

    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    required_fields = (
        "id",
        "question_vi",
        "question_en",
        "difficulty",
        "category",
        "split",
        "expected_outcome",
    )
    for index, item in enumerate(questions):
        if not isinstance(item, dict):
            raise _contract_error(index, "phải là một đối tượng JSON.")

        unknown_fields = sorted(set(item) - BENCHMARK_FIELDS)
        if unknown_fields:
            raise _contract_error(
                index,
                "có trường không thuộc hợp đồng: " + ", ".join(unknown_fields),
            )
        for field in required_fields:
            if not _is_nonempty_string(item.get(field)):
                raise _contract_error(index, f"thiếu hoặc rỗng trường '{field}'.")

        benchmark_id = item["id"].strip()
        if benchmark_id in seen_ids:
            raise _contract_error(index, f"id '{benchmark_id}' bị trùng.")
        seen_ids.add(benchmark_id)

        difficulty = item["difficulty"].strip()
        if difficulty not in SUPPORTED_DIFFICULTIES:
            raise _contract_error(index, "difficulty không hợp lệ.")
        category = item["category"].strip()
        if category not in SUPPORTED_CATEGORIES:
            raise _contract_error(index, "category không hợp lệ.")
        split = item["split"].strip()
        if split not in SUPPORTED_SPLITS:
            raise _contract_error(index, "split phải là development hoặc test.")
        expected_outcome = item["expected_outcome"].strip()
        if expected_outcome not in SUPPORTED_OUTCOMES:
            raise _contract_error(
                index, "expected_outcome phải là query hoặc clarify_or_refuse."
            )

        if expected_outcome == QUERY_OUTCOME:
            if not _is_nonempty_string(item.get("reference_sql")):
                raise _contract_error(index, "ca query phải có reference_sql không rỗng.")
            if "evaluation_note" in item:
                raise _contract_error(index, "ca query không được có evaluation_note.")
        else:
            if "reference_sql" in item:
                raise _contract_error(
                    index, "ca clarify_or_refuse không được có reference_sql."
                )
            if not _is_nonempty_string(item.get("evaluation_note")):
                raise _contract_error(
                    index,
                    "ca clarify_or_refuse phải có evaluation_note không rỗng.",
                )

        # Normalize harmless surrounding whitespace once, after validation.
        normalized = dict(item)
        for field in required_fields:
            normalized[field] = normalized[field].strip()
        if "reference_sql" in normalized:
            normalized["reference_sql"] = normalized["reference_sql"].strip()
        if "evaluation_note" in normalized:
            normalized["evaluation_note"] = normalized["evaluation_note"].strip()
        validated.append(normalized)
    return validated


def load_questions(path: Path = BENCHMARK_PATH) -> list[dict[str, Any]]:
    """Read the shared benchmark used for all experimental variants."""
    try:
        content = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Không đọc được bộ benchmark: {path}") from error
    if not isinstance(content, list):
        raise ValueError("Bộ benchmark phải là một mảng JSON.")
    return validate_benchmark_contract(content)


def _question_cases(questions: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    """Yield one independent evaluation case for every available language.

    ``id`` remains the stable business-question identifier while ``case_id``
    makes the language variant unambiguous in a flat report. Inputs have
    already passed ``validate_benchmark_contract``, so neither language is
    silently skipped and no outcome is inferred.
    """
    for item in questions:
        benchmark_id = item["id"]
        expected_outcome = item["expected_outcome"]
        for language, field in LANGUAGE_FIELDS:
            question = item[field]

            case: dict[str, Any] = {
                "id": benchmark_id,
                "case_id": f"{benchmark_id}_{language}",
                "language": language,
                "question_language": language,
                "question": question.strip(),
                "difficulty": item["difficulty"],
                "category": item["category"],
                "split": item["split"],
                "expected_outcome": expected_outcome,
            }
            if "evaluation_note" in item:
                case["evaluation_note"] = item["evaluation_note"]
            # A query case without a reference is reported as malformed rather
            # than borrowing a reference from another record. In particular,
            # clarify_or_refuse records intentionally have no reference_sql.
            if expected_outcome == QUERY_OUTCOME:
                case["reference_sql"] = item["reference_sql"]
            yield case


def _candidate_or_error(
    question: str, schema: str, context: str | None = None
) -> dict[str, Any]:
    """Keep one failing LLM generation from stopping the whole benchmark."""
    try:
        return {"sql": generate_sql(question, schema, context=context)}
    except Exception:
        # Provider and parser exceptions may expose URLs, local paths, or
        # credentials. The detailed exception stays out of the JSON report.
        return {
            "error_code": "generation_failed",
            "error": "Không thể tạo SQL cho ca này.",
        }


def _validation_payload(sql: str, db_path: Path) -> tuple[dict[str, Any], str | None]:
    """Validate SQL and return only normalized SQL that passed the validator."""
    try:
        result = validate_sql(sql, db_path=db_path)
    except Exception:  # Keep the batch report usable without leaking internals.
        return (
            {
                "valid": False,
                "sql": None,
                "errors": ["Không thể xác thực SQL do lỗi nội bộ của evaluator."],
                "warnings": [],
            },
            None,
        )

    valid = bool(getattr(result, "valid", False))
    normalized_sql = getattr(result, "sql", None)
    if not isinstance(normalized_sql, str) or not normalized_sql.strip():
        normalized_sql = None
    # A positive flag alone is not an approval: only a normalized SQL string
    # issued by the validator can cross the execution boundary.
    if valid and normalized_sql is None:
        valid = False
        validator_errors = [
            "Validator không trả về SQL đã chuẩn hóa; SQL không được chấp thuận."
        ]
    else:
        validator_errors = list(getattr(result, "errors", []) or [])
    payload = {
        "valid": valid,
        "sql": normalized_sql,
        "errors": validator_errors,
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
    """Execute only SQL that has already received a positive validator result.

    Result comparison is intentionally bounded. A truncated result cannot be
    compared safely, so an oversized result is reported as non-comparable
    rather than fingerprinting only its first rows.
    """
    if not isinstance(validated_sql, str) or not validated_sql.strip():
        return (
            {"status": "skipped", "reason": "validation_failed"},
            None,
        )

    try:
        connection = connect_read_only(db_path)
        try:
            cursor = connection.execute(validated_sql)
            rows = list(cursor.fetchmany(MAX_RESULT_ROWS + 1))
            description = getattr(cursor, "description", None) or []
        finally:
            connection.close()
        if len(rows) > MAX_RESULT_ROWS:
            return (
                {
                    "status": "not_comparable",
                    "reason": "result_row_limit_exceeded",
                    "row_limit": MAX_RESULT_ROWS,
                    "comparable": False,
                },
                None,
            )

        fingerprints = _row_fingerprints(rows)
        columns = [str(column[0]) for column in description if column]
        return (
            {
                "status": "completed",
                "row_count": len(rows),
                "columns": columns,
                "result_digest": _result_digest(fingerprints),
                "comparable": True,
            },
            fingerprints,
        )
    except Exception:
        return (
            {
                "status": "error",
                "error_code": "execution_failed",
                "error": "Không thể thực thi SQL đã được xác thực.",
            },
            None,
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


def _cached_reference_assessment(
    case: dict[str, Any],
    db_path: Path,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Reuse a bilingual record's reference assessment within one run.

    The cache is deliberately scoped to one ``compare_questions`` invocation
    and keyed by the exact normalized reference SQL, so it never crosses a
    database path or benchmark run. A deep copy prevents per-case report
    handling from mutating the cached execution fingerprints.
    """
    reference_sql = case["reference_sql"]
    if reference_sql not in cache:
        cache[reference_sql] = _reference_assessment(case, db_path)
    return copy.deepcopy(cache[reference_sql])


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
            "reason": (
                "reference_not_comparable"
                if reference_execution.get("status") == "not_comparable"
                else "reference_not_executable"
            ),
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


def _rag_validation_gate_view(rag: dict[str, Any]) -> dict[str, Any]:
    """Expose RAG's validation gate without representing it as a method."""
    view = {
        "view_of_method": "rag",
        "is_independent_llm_method": False,
        "candidate_reused": True,
        "validation": copy.deepcopy(rag.get("validation")),
        "execution": copy.deepcopy(rag.get("execution")),
    }
    if "matches_reference" in rag:
        view["matches_reference"] = rag["matches_reference"]
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


def _empty_validation_gate_summary() -> dict[str, Any]:
    """Summarize the RAG safety gate, not another model's quality metrics."""
    return {
        "view_of_method": "rag",
        "is_independent_llm_method": False,
        "query_case_count": 0,
        "candidate_sql_count": 0,
        "validator_approved_count": 0,
        "validator_rejected_count": 0,
        "completed_comparison_count": 0,
        "non_comparable_count": 0,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate transparent metrics without pretending to score refusals."""
    method_summaries = {method: _empty_method_summary() for method in METHOD_NAMES}
    validation_gate = _empty_validation_gate_summary()
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

        validation_gate["query_case_count"] += 1
        rag = result.get("rag")
        if isinstance(rag, dict):
            if isinstance(rag.get("sql"), str) and rag["sql"].strip():
                validation_gate["candidate_sql_count"] += 1
            validation = rag.get("validation")
            if isinstance(validation, dict):
                if validation.get("valid"):
                    validation_gate["validator_approved_count"] += 1
                elif isinstance(rag.get("sql"), str) and rag["sql"].strip():
                    validation_gate["validator_rejected_count"] += 1
            execution = rag.get("execution")
            if isinstance(execution, dict):
                if execution.get("status") == "completed":
                    validation_gate["completed_comparison_count"] += 1
                elif execution.get("status") == "not_comparable":
                    validation_gate["non_comparable_count"] += 1

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
        "validation_gate": validation_gate,
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
    """Compare schema-only and RAG candidates on validated bilingual cases.

    Every generated candidate goes through ``validate_sql``. The only
    execution helper accepts normalized SQL returned by a successful validator
    call and uses DuckDB's local read-only connection with external access
    disabled.
    """
    questions = validate_benchmark_contract(questions)
    db_path = Path(db_path)
    cases = list(_question_cases(questions))
    schema = ""
    if any(case["expected_outcome"] == QUERY_OUTCOME for case in cases):
        try:
            # Keep the schema prompt aligned with the exact database being
            # validated and executed, including injected test databases.
            schema = get_schema(db_path)
        except Exception:
            raise RuntimeError("Không thể tải schema database để chạy benchmark.") from None

    results: list[dict[str, Any]] = []
    reference_cache: dict[str, dict[str, Any]] = {}
    for case in cases:
        expected_outcome = case["expected_outcome"]
        if expected_outcome == CLARIFY_OR_REFUSE_OUTCOME:
            # This class is intentionally separate from Text-to-SQL scoring.
            # It must not consume an LLM call or create a synthetic SQL answer.
            results.append(
                {
                    **case,
                    "manual_review_required": True,
                    "manual_review": {
                        "status": "required",
                        "reason": "clarify_or_refuse_requires_manual_review",
                        "scored_automatically": False,
                        "evaluation_note": case["evaluation_note"],
                    },
                }
            )
            continue

        reference: dict[str, Any] | None = None
        if expected_outcome == QUERY_OUTCOME:
            reference = _cached_reference_assessment(case, db_path, reference_cache)

        schema_only = _assess_candidate(
            _candidate_or_error(case["question"], schema),
            expected_outcome=expected_outcome,
            reference=reference,
            db_path=db_path,
        )

        try:
            context = retrieve_context(case["question"], model_name=model_name)
        except Exception:
            rag = {
                "retrieval_error_code": "retrieval_failed",
                "retrieval_error": "Không thể truy xuất ngữ cảnh RAG cho ca này.",
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
            # This is a view over ``rag``, not a fresh model response or a
            # third experimental method.
            "rag_validation_gate": _rag_validation_gate_view(rag),
        }
        if reference is not None:
            result["reference"] = _public_reference(reference)
        else:
            result["manual_review_required"] = True
        results.append(result)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Execution-based comparison of schema-only Text-to-SQL and RAG. "
            "The RAG validation gate is a non-method view. Only "
            "validator-approved SQL is executed locally."
        ),
        "results": results,
        "summary": _build_summary(results),
    }


def _select_questions(
    questions: list[dict[str, Any]], *, split: str, question_ids: list[str] | None
) -> list[dict[str, Any]]:
    """Select records before bilingual expansion.

    Explicit question IDs take precedence over the default development split,
    which makes a single holdout case reproducible without remembering an
    additional ``--split test`` flag.
    """
    if question_ids:
        selected_ids = set(question_ids)
        available_ids = {item["id"] for item in questions}
        missing_ids = sorted(selected_ids - available_ids)
        if missing_ids:
            raise ValueError("Không tìm thấy mã benchmark: " + ", ".join(missing_ids))
        return [item for item in questions if item["id"] in selected_ids]
    if split == "all":
        return list(questions)
    return [item for item in questions if item["split"] == split]


def main() -> None:
    """Run the shared benchmark and save auditable comparison data."""
    configure_utf8_output()
    parser = argparse.ArgumentParser(
        description="So sánh Text-to-SQL schema-only và RAG qua validation gate"
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
    parser.add_argument(
        "--split",
        choices=("development", "test", "all"),
        default="development",
        help="Tập benchmark để chạy khi không truyền --question-id (mặc định: development)",
    )
    arguments = parser.parse_args()
    questions = load_questions(arguments.benchmark)
    questions = _select_questions(
        questions, split=arguments.split, question_ids=arguments.question_ids
    )
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
    gate = summary["validation_gate"]
    print(
        "RAG validation gate (không phải LLM method): "
        f"approved={gate['validator_approved_count']}, "
        f"rejected={gate['validator_rejected_count']}, "
        f"non_comparable={gate['non_comparable_count']}"
    )
    if summary["clarify_or_refuse_case_count"]:
        print(
            "Các ca clarify_or_refuse được giữ để đánh giá thủ công; "
            "không thực thi hoặc ép reference_sql."
        )


if __name__ == "__main__":
    main()
