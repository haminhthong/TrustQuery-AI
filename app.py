"""Streamlit interface for safe, reviewable Olist Text-to-SQL queries.

Run from the repository root after installing the project requirements:

    streamlit run app.py

The application never executes a candidate query unless the SQL validator has
approved it. DuckDB is opened in read-only mode for every execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.database.read_only import connect_read_only


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "database" / "olist.duckdb"
MAX_DISPLAY_ROWS = 1_000
MAX_CHART_ROWS = 50
_METRIC_NAME_HINTS = (
    "average",
    "avg",
    "count",
    "delivery",
    "item",
    "max",
    "min",
    "payment",
    "percent",
    "quantity",
    "rate",
    "ratio",
    "revenue",
    "sales",
    "score",
    "sold",
    "sum",
    "total",
    "value",
)


@dataclass
class QueryOutcome:
    """Everything the UI needs to explain a question's outcome safely."""

    question: str
    sql: str | None = None
    context: str | None = None
    valid: bool = False
    errors: list[str] | None = None
    warnings: list[str] | None = None
    dataframe: Any | None = None
    truncated: bool = False


def execute_read_only_query(sql: str, db_path: Path = DB_PATH) -> tuple[Any, bool]:
    """Execute a validated query against DuckDB and cap rows shown in the UI.

    The wrapping ``LIMIT`` protects the browser from unexpectedly large result
    tables. It does not alter the generated query displayed to the user, and
    this function is only reached after validation succeeds.
    """
    if not db_path.is_file():
        raise FileNotFoundError(
            "Chưa có database Olist. Hãy chạy python src/database/load_data.py trước."
        )

    display_sql = (
        "SELECT * FROM (\n"
        f"{sql}\n"
        ") AS trustquery_result\n"
        f"LIMIT {MAX_DISPLAY_ROWS + 1}"
    )
    connection = connect_read_only(db_path)
    try:
        dataframe = connection.execute(display_sql).fetchdf()
    finally:
        connection.close()

    truncated = len(dataframe.index) > MAX_DISPLAY_ROWS
    if truncated:
        dataframe = dataframe.iloc[:MAX_DISPLAY_ROWS].copy()
    return dataframe, truncated


def answer_question(question: str, *, use_rag: bool) -> QueryOutcome:
    """Generate, validate, then (only if approved) execute a candidate query."""
    outcome = QueryOutcome(question=question, errors=[], warnings=[])
    try:
        from src.llm.text_to_sql import generate_sql, get_schema
        from src.validation.sql_validator import validate_sql

        schema = get_schema(DB_PATH)
        context = ""
        if use_rag:
            from src.rag.retriever import retrieve_context

            context = retrieve_context(question)
            outcome.context = context

        outcome.sql = generate_sql(question=question, schema=schema, context=context)
        validation = validate_sql(outcome.sql, db_path=DB_PATH)
        outcome.valid = bool(validation.valid)
        outcome.errors = list(validation.errors)
        outcome.warnings = list(validation.warnings)
        if not validation.valid:
            return outcome
        if not validation.sql:
            outcome.valid = False
            outcome.errors.append("Validator không trả về SQL an toàn để thực thi.")
            return outcome

        outcome.dataframe, outcome.truncated = execute_read_only_query(
            validation.sql, DB_PATH
        )
        return outcome
    except Exception as error:  # The UI must make every failure understandable.
        outcome.valid = False
        outcome.errors.append(str(error) or error.__class__.__name__)
        return outcome


def _choose_chart_columns(dataframe: Any) -> tuple[str, str] | None:
    """Choose a small, conservative bar chart from a tabular result."""
    if not 2 <= len(dataframe.index) <= MAX_CHART_ROWS or len(dataframe.columns) < 2:
        return None

    numeric_columns = [str(column) for column in dataframe.select_dtypes(include="number").columns]
    metric_columns = [
        column
        for column in numeric_columns
        if any(hint in column.lower() for hint in _METRIC_NAME_HINTS)
    ]
    if not metric_columns:
        return None

    # Prefer the last metric as the y-axis. For a result such as year +
    # total_orders, the non-metric year naturally becomes the x-axis.
    measure = metric_columns[-1]
    candidate_x = [column for column in dataframe.columns if column != measure]
    if not candidate_x:
        return None
    return str(candidate_x[0]), str(measure)


def render_outcome(st: Any, outcome: QueryOutcome, *, show_context: bool) -> None:
    """Render one assistant response without granting it execution authority."""
    if outcome.sql:
        st.markdown("**SQL được tạo**")
        st.code(outcome.sql, language="sql")

    if outcome.valid:
        st.success("SQL đã vượt qua kiểm tra cú pháp, schema, an toàn và quy tắc nghiệp vụ.")
    elif outcome.sql:
        st.error("SQL bị từ chối — không có truy vấn nào được thực thi.")

    for warning in outcome.warnings or []:
        st.warning(warning)
    for error in outcome.errors or []:
        st.error(error)

    if show_context and outcome.context:
        with st.expander("Ngữ cảnh nghiệp vụ RAG đã dùng"):
            st.markdown(outcome.context)

    if outcome.dataframe is None:
        if not outcome.errors and not outcome.sql:
            st.info("Không thể tạo câu trả lời từ câu hỏi này.")
        return

    st.markdown("**Kết quả**")
    st.dataframe(outcome.dataframe, use_container_width=True, hide_index=True)
    if outcome.truncated:
        st.info(f"Chỉ hiển thị {MAX_DISPLAY_ROWS:,} dòng đầu để bảo vệ giao diện.")

    chart_columns = _choose_chart_columns(outcome.dataframe)
    if chart_columns:
        x_column, y_column = chart_columns
        with st.expander("Biểu đồ", expanded=True):
            chart_data = outcome.dataframe[[x_column, y_column]].set_index(x_column)
            st.bar_chart(chart_data)


def main() -> None:
    """Create the interactive Streamlit page."""
    try:
        import streamlit as st
    except ImportError as error:
        raise RuntimeError(
            "Thiếu Streamlit. Hãy chạy: python -m pip install -r requirements.txt"
        ) from error

    st.set_page_config(page_title="TrustQuery AI", page_icon="🔎", layout="wide")
    st.title("TrustQuery AI")
    st.caption(
        "Hỏi dữ liệu Olist bằng ngôn ngữ tự nhiên. SQL chỉ chạy sau khi validator chấp thuận."
    )

    with st.sidebar:
        st.subheader("Cài đặt")
        use_rag = st.toggle("Dùng quy tắc nghiệp vụ RAG", value=True)
        show_context = st.checkbox("Hiển thị ngữ cảnh RAG", value=False)
        st.caption("Kết quả luôn chạy trên DuckDB chỉ đọc; tối đa 1.000 dòng được hiển thị.")
        if st.button("Xóa hội thoại", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.write(message["content"])
            else:
                render_outcome(st, message["outcome"], show_context=show_context)

    question = st.chat_input("Ví dụ: Có bao nhiêu khách hàng đã mua lại?")
    if not question:
        return

    cleaned_question = question.strip()
    if not cleaned_question:
        st.warning("Câu hỏi không được để trống.")
        return

    st.session_state.messages.append({"role": "user", "content": cleaned_question})
    with st.chat_message("user"):
        st.write(cleaned_question)

    with st.chat_message("assistant"):
        with st.spinner("Đang tạo, kiểm tra và chạy SQL an toàn..."):
            outcome = answer_question(cleaned_question, use_rag=use_rag)
        render_outcome(st, outcome, show_context=show_context)
    st.session_state.messages.append({"role": "assistant", "outcome": outcome})


if __name__ == "__main__":
    main()
