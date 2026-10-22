"""Generate a side-by-side Text-to-SQL benchmark for schema-only and RAG modes.

This is an evaluation aid, not an automatic SQL correctness judge.  It writes
both candidate queries, the retrieved context, and the reference query so a
reviewer can assess whether RAG reduced business-definition mistakes.

Run after building the local RAG index and configuring ``.env``:

    python -m src.evaluation.compare_rag
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.console import configure_utf8_output
from src.llm.text_to_sql import generate_sql, get_schema
from src.rag.build_index import EMBEDDING_MODEL
from src.rag.retriever import retrieve_context
from src.validation.sql_safety import UnsafeSQLError


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = ROOT / "benchmarks" / "questions.json"
DEFAULT_OUTPUT_PATH = ROOT / "benchmarks" / "results" / "rag_comparison.json"


def load_questions(path: Path = BENCHMARK_PATH) -> list[dict[str, Any]]:
    """Read the shared benchmark used for both experimental variants."""
    try:
        content = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Không đọc được bộ benchmark: {path}") from error
    if not isinstance(content, list):
        raise ValueError("Bộ benchmark phải là một mảng JSON.")
    return [item for item in content if isinstance(item, dict)]


def _candidate_or_error(question: str, schema: str, context: str | None = None) -> dict[str, str]:
    """Keep one failing candidate from stopping the rest of an A/B benchmark."""
    try:
        return {"sql": generate_sql(question, schema, context=context)}
    except (RuntimeError, UnsafeSQLError) as error:
        return {"error": str(error)}


def compare_questions(
    questions: list[dict[str, Any]], *, model_name: str = EMBEDDING_MODEL
) -> dict[str, Any]:
    """Generate schema-only and RAG-assisted SQL for exactly the same questions."""
    schema = get_schema()
    results: list[dict[str, Any]] = []
    for item in questions:
        question = str(item.get("question_vi") or item.get("question_en") or "").strip()
        if not question:
            continue
        try:
            context = retrieve_context(question, model_name=model_name)
        except (RuntimeError, ValueError) as error:
            rag_result: dict[str, str] = {"retrieval_error": str(error)}
        else:
            rag_result = {
                "context": context,
                **_candidate_or_error(question, schema, context=context),
            }
        results.append(
            {
                "id": item.get("id"),
                "question": question,
                "reference_sql": item.get("reference_sql"),
                "schema_only": _candidate_or_error(question, schema),
                "rag": rag_result,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Manual comparison of schema-only Text-to-SQL with the same "
            "questions augmented by retrieved business rules."
        ),
        "results": results,
    }


def main() -> None:
    """Run the shared benchmark twice and save auditable comparison data."""
    configure_utf8_output()
    parser = argparse.ArgumentParser(
        description="So sánh Text-to-SQL schema-only và RAG trên cùng benchmark"
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
    print(
        f"Đã tạo báo cáo A/B cho {len(report['results'])} câu hỏi: {arguments.output}"
    )
    print("Hãy đối chiếu từng candidate SQL với reference_sql; không đánh giá bằng so sánh chuỗi.")


if __name__ == "__main__":
    main()
