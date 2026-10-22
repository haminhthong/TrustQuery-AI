"""Streamlit interface for safe, reviewable Olist Text-to-SQL queries.

Run from the repository root after installing the project requirements:

    python -m streamlit run app.py

The application never executes a candidate query unless the SQL validator has
approved it. DuckDB is opened read-only with external resource access disabled.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.database.read_only import connect_read_only


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "database" / "olist.duckdb"
BENCHMARK_PATH = ROOT / "benchmarks" / "questions.json"
MAX_DISPLAY_ROWS = 1_000
MAX_CHART_ROWS = 50
LOGGER = logging.getLogger(__name__)

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

_SAMPLE_PRESETS = [
    {
        "label": "🛒 Tổng đơn hàng",
        "question": "Tổng số đơn hàng là bao nhiêu?",
        "id": "Q001",
    },
    {
        "label": "📦 Đơn đã giao",
        "question": "Có bao nhiêu đơn đã được giao?",
        "id": "Q002",
    },
    {
        "label": "🏆 Top 5 danh mục",
        "question": "Top 5 danh mục có nhiều sản phẩm được bán nhất?",
        "id": "Q004",
    },
    {
        "label": "🔄 Khách mua lại",
        "question": "Có bao nhiêu khách hàng đã mua lại?",
        "id": "Q006",
    },
    {
        "label": "⏰ Đơn giao trễ",
        "question": "Có bao nhiêu đơn hàng được giao trễ?",
        "id": "Q008",
    },
    {
        "label": "💰 Giá trị thanh toán theo năm",
        "question": "Giá trị thanh toán của các đơn đã giao theo năm?",
        "id": "Q005",
    },
]


@dataclass
class QueryOutcome:
    """Everything the UI needs to explain a question's outcome safely."""

    question: str
    sql: str | None = None
    context: str | None = None
    valid: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dataframe: Any | None = None
    truncated: bool = False
    execution_time_ms: float = 0.0
    explain_plan: str | None = None
    is_demo: bool = False


_SAFE_RUNTIME_ERROR_PREFIXES = (
    "Chưa có chỉ mục",
    "Chỉ mục được tạo bằng model",
    "Database không có bảng",
    "Database Olist không chứa bảng",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "Gemini không trả về",
    "Thiếu thư viện",
    "Thiếu sqlglot",
)
_GENERIC_QUERY_ERROR = (
    "Không thể hoàn tất câu hỏi. Hãy kiểm tra database Olist, chỉ mục RAG "
    "và cấu hình Gemini rồi thử lại."
)


def _user_facing_error(error: Exception) -> str:
    """Return actionable expected errors without exposing unexpected internals."""
    message = str(error).strip()
    if isinstance(error, (FileNotFoundError, ValueError)):
        return message or "Dữ liệu đầu vào không hợp lệ."
    if isinstance(error, RuntimeError) and message.startswith(_SAFE_RUNTIME_ERROR_PREFIXES):
        return message
    return _GENERIC_QUERY_ERROR


def execute_read_only_query(sql: str, db_path: Path = DB_PATH) -> tuple[Any, bool]:
    """Execute a validated query against DuckDB and cap rows shown in the UI.

    The wrapping ``LIMIT`` protects the browser from unexpectedly large result
    tables. It does not alter the generated query displayed to the user, and
    this function is only reached after validation succeeds.
    """
    if not db_path.is_file():
        raise FileNotFoundError(
            "Chưa có database Olist. Hãy chạy python -m src.database.load_data trước."
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


def get_execution_plan(sql: str, db_path: Path = DB_PATH) -> str | None:
    """Safely obtain DuckDB's query execution plan for validated SQL."""
    try:
        connection = connect_read_only(db_path)
        try:
            rows = connection.execute(f"EXPLAIN {sql}").fetchall()
            if rows and len(rows[0]) > 1:
                return str(rows[0][1])
            return None
        finally:
            connection.close()
    except Exception:
        return None


def _find_benchmark_reference_sql(question: str) -> str | None:
    """Find verified reference SQL for benchmark questions if demo fallback is used."""
    if not BENCHMARK_PATH.is_file():
        return None
    try:
        questions = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
        norm_q = question.strip().lower()
        # 1. Exact match
        for item in questions:
            if item.get("question_vi", "").strip().lower() == norm_q:
                return str(item.get("reference_sql"))
            if item.get("question_en", "").strip().lower() == norm_q:
                return str(item.get("reference_sql"))
        # 2. Semantic keyword matching for demo resilience
        if "mua lại" in norm_q or "repeat" in norm_q:
            return next((str(q["reference_sql"]) for q in questions if q.get("id") == "Q006"), None)
        if "giao trễ" in norm_q or "trễ" in norm_q or "late" in norm_q:
            return next((str(q["reference_sql"]) for q in questions if q.get("id") == "Q008"), None)
        if "top" in norm_q or "danh mục" in norm_q or "categories" in norm_q:
            return next((str(q["reference_sql"]) for q in questions if q.get("id") == "Q004"), None)
        if "đã được giao" in norm_q or "đã giao" in norm_q or "delivered" in norm_q:
            return next((str(q["reference_sql"]) for q in questions if q.get("id") == "Q002"), None)
        if "tổng" in norm_q and "đơn" in norm_q or "how many orders" in norm_q:
            return next((str(q["reference_sql"]) for q in questions if q.get("id") == "Q001"), None)
        if "thanh toán" in norm_q or "payment" in norm_q:
            return next((str(q["reference_sql"]) for q in questions if q.get("id") == "Q005"), None)
        if "duy nhất" in norm_q or "unique" in norm_q:
            return next((str(q["reference_sql"]) for q in questions if q.get("id") == "Q007"), None)
        if "năm" in norm_q or "year" in norm_q:
            return next((str(q["reference_sql"]) for q in questions if q.get("id") == "Q003"), None)
    except Exception:
        pass
    return None


def answer_question(
    question: str,
    *,
    use_rag: bool,
    api_key: str | None = None,
    model: str | None = None,
    force_demo_sql: str | None = None,
) -> QueryOutcome:
    """Generate, validate, then (only if approved) execute a candidate query."""
    outcome = QueryOutcome(question=question)
    start_time = time.perf_counter()
    try:
        from src.llm.text_to_sql import generate_sql, get_schema
        from src.validation.sql_validator import validate_sql

        schema = get_schema(DB_PATH)
        context = ""
        if use_rag:
            from src.rag.retriever import retrieve_context

            context = retrieve_context(question)
            outcome.context = context

        # Check if caller specified reference demo SQL or if API key is absent
        candidate_sql = force_demo_sql
        if not candidate_sql:
            active_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
            if not active_key:
                # If question matches a known benchmark query, use it as demo mode
                reference_sql = _find_benchmark_reference_sql(question)
                if reference_sql:
                    candidate_sql = reference_sql
                    outcome.is_demo = True

        if candidate_sql:
            outcome.sql = candidate_sql
        else:
            kwargs: dict[str, Any] = {
                "question": question,
                "schema": schema,
                "context": context,
            }
            if api_key:
                kwargs["api_key"] = api_key
            if model:
                kwargs["model"] = model
            outcome.sql = generate_sql(**kwargs)

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
        outcome.explain_plan = get_execution_plan(validation.sql, DB_PATH)
        outcome.execution_time_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return outcome
    except Exception as error:  # Keep unexpected implementation details out of the UI.
        message = _user_facing_error(error)
        if message == _GENERIC_QUERY_ERROR:
            LOGGER.exception("Failed to answer a TrustQuery question")
        else:
            LOGGER.info("TrustQuery request could not be completed: %s", message)
        outcome.valid = False
        outcome.errors.append(message)
        outcome.execution_time_ms = round((time.perf_counter() - start_time) * 1000, 2)
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


def _render_custom_css(st: Any) -> None:
    """Inject modern, sleek dark/glassmorphic styling tokens."""
    st.markdown(
        """
        <style>
        /* Modern App Container Styling */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        /* Glassmorphism Cards */
        .tq-header-card {
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.8) 100%);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.2), 0 8px 10px -6px rgba(0, 0, 0, 0.2);
            backdrop-filter: blur(12px);
        }

        .tq-status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            border-radius: 9999px;
            font-size: 0.8rem;
            font-weight: 600;
            margin-right: 8px;
            margin-top: 6px;
        }

        .badge-safe {
            background: rgba(16, 185, 129, 0.15);
            color: #10b981;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .badge-ast {
            background: rgba(14, 165, 233, 0.15);
            color: #38bdf8;
            border: 1px solid rgba(14, 165, 233, 0.3);
        }

        .badge-rag {
            background: rgba(168, 85, 247, 0.15);
            color: #c084fc;
            border: 1px solid rgba(168, 85, 247, 0.3);
        }

        .badge-demo {
            background: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.3);
        }

        .tq-metric-card {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 12px 16px;
            text-align: center;
        }

        .tq-metric-num {
            font-size: 1.5rem;
            font-weight: 700;
            color: #38bdf8;
        }

        .tq-metric-label {
            font-size: 0.75rem;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        /* Code block font */
        pre, code {
            font-family: 'JetBrains Mono', monospace !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_outcome(st: Any, outcome: QueryOutcome, *, show_context: bool) -> None:
    """Render one assistant response with tabs, execution plans, and metrics."""
    if outcome.is_demo:
        st.markdown(
            '<span class="tq-status-badge badge-demo">⚡ CHẾ ĐỘ DEMO BENCHMARK (Tham chiếu chuẩn)</span>',
            unsafe_allow_html=True,
        )

    # Validation Outcome Alert
    if outcome.valid:
        st.success("✅ **SQL hợp lệ:** Đã vượt qua kiểm tra cú pháp AST, allowlist schema, an toàn bảo mật và bẫy nghiệp vụ.")
    elif outcome.sql:
        st.error("❌ **SQL bị từ chối:** Truy vấn không vượt qua bộ kiểm tra an toàn — không có lệnh nào được chạy.")

    for warning in outcome.warnings:
        st.warning(f"⚠️ {warning}")
    for error in outcome.errors:
        st.error(f"🚫 {error}")

    # Tabs for in-depth inspection
    tab_labels = ["📊 Kết quả & Trực quan", "📝 SQL & Kế hoạch (EXPLAIN)", "🛡️ Chi tiết Xác thực"]
    if outcome.context or show_context:
        tab_labels.append("🧠 Ngữ cảnh RAG")

    tabs = st.tabs(tab_labels)

    # Tab 1: Results & Charts
    with tabs[0]:
        if outcome.dataframe is not None:
            # Metrics Row
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(
                    f'<div class="tq-metric-card"><div class="tq-metric-num">{len(outcome.dataframe):,}</div>'
                    f'<div class="tq-metric-label">Số dòng kết quả</div></div>',
                    unsafe_allow_html=True,
                )
            with col2:
                st.markdown(
                    f'<div class="tq-metric-card"><div class="tq-metric-num">{len(outcome.dataframe.columns)}</div>'
                    f'<div class="tq-metric-label">Số cột dữ liệu</div></div>',
                    unsafe_allow_html=True,
                )
            with col3:
                st.markdown(
                    f'<div class="tq-metric-card"><div class="tq-metric-num">{outcome.execution_time_ms} ms</div>'
                    f'<div class="tq-metric-label">Thời gian thực thi</div></div>',
                    unsafe_allow_html=True,
                )

            st.write("")
            st.dataframe(outcome.dataframe, use_container_width=True, hide_index=True)

            if outcome.truncated:
                st.info(f"ℹ️ Chỉ hiển thị {MAX_DISPLAY_ROWS:,} dòng đầu tiên để tối ưu hiệu năng trình duyệt.")

            # CSV Download
            csv_data = outcome.dataframe.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Tải xuống dữ liệu (CSV)",
                data=csv_data,
                file_name="trustquery_result.csv",
                mime="text/csv",
                key=f"dl_{hash(outcome.question)}_{time.time()}",
            )

            # Smart Charting
            chart_columns = _choose_chart_columns(outcome.dataframe)
            if chart_columns:
                x_col, y_col = chart_columns
                st.markdown(f"**📈 Trực quan hóa ({x_col} ➔ {y_col})**")
                chart_data = outcome.dataframe[[x_col, y_col]].set_index(x_col)
                chart_type = st.radio(
                    "Loại biểu đồ:",
                    ["Cột (Bar)", "Đường (Line)", "Miền (Area)"],
                    horizontal=True,
                    key=f"chart_type_{hash(outcome.question)}",
                )
                if chart_type == "Cột (Bar)":
                    st.bar_chart(chart_data)
                elif chart_type == "Đường (Line)":
                    st.line_chart(chart_data)
                else:
                    st.area_chart(chart_data)
        elif not outcome.errors and not outcome.sql:
            st.info("Không thể tạo câu trả lời từ câu hỏi này.")

    # Tab 2: SQL & EXPLAIN Plan
    with tabs[1]:
        if outcome.sql:
            st.markdown("**Câu lệnh SQL ứng viên:**")
            st.code(outcome.sql, language="sql")

        if outcome.explain_plan:
            with st.expander("🔍 Xem DuckDB Execution Plan (EXPLAIN Tree)", expanded=False):
                st.code(outcome.explain_plan, language="text")

    # Tab 3: Validator Verification
    with tabs[2]:
        st.markdown("**Trạng thái các lớp kiểm tra (Verification Layers):**")
        st.write("1. **Cú pháp AST (SQLGlot):** " + ("✅ Hợp lệ (SELECT / CTE duy nhất)" if outcome.sql and not outcome.errors else "❓ Cần kiểm tra"))
        st.write("2. **Allowlist Schema Olist:** " + ("✅ Đã đối chiếu 9 bảng và cột DuckDB" if outcome.valid else "❌ Có vi phạm hoặc chưa duyệt"))
        st.write("3. **Chặn DDL / DML / External:** ✅ Không có quyền ghi hoặc đọc ngoài (Chỉ đọc `olist.duckdb`)")
        st.write("4. **Quy tắc Nghiệp vụ (Fan-out JOIN):** " + ("⚠️ Phát hiện cảnh báo" if outcome.warnings else "✅ Không phát hiện bẫy nhân dòng"))

    # Tab 4: RAG Context
    if len(tabs) > 3 and outcome.context:
        with tabs[3]:
            st.markdown("**Đoạn trích tri thức RAG từ kho tài liệu Git:**")
            st.markdown(outcome.context)


def _render_schema_explorer(st: Any) -> None:
    """Interactive Olist database schema browser."""
    st.subheader("🗄️ Khám phá Cơ sở Dữ liệu Olist")
    st.caption("Xem thông tin 9 bảng dữ liệu, cấu trúc cột và dữ liệu thực tế trong DuckDB.")

    if not DB_PATH.is_file():
        st.error("Chưa có database Olist. Hãy chạy `python -m src.database.load_data` trước.")
        return

    from src.database.load_data import TABLES

    table_names = list(TABLES.keys())
    selected_table = st.selectbox("Chọn bảng cần kiểm tra:", table_names, index=0)

    try:
        connection = connect_read_only(DB_PATH)
        try:
            row_count = connection.execute(f"SELECT COUNT(*) FROM {selected_table}").fetchone()[0]
            desc = connection.execute(f"DESCRIBE {selected_table}").fetchdf()
            sample = connection.execute(f"SELECT * FROM {selected_table} LIMIT 10").fetchdf()
        finally:
            connection.close()

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Tổng số dòng", f"{row_count:,}")
        with col2:
            st.metric("Số lượng cột", len(desc))

        st.markdown(f"**Cấu trúc cột của `{selected_table}`:**")
        st.dataframe(desc[["column_name", "column_type", "null"]], use_container_width=True, hide_index=True)

        st.markdown(f"**Xem trước 10 dòng đầu tiên của `{selected_table}`:**")
        st.dataframe(sample, use_container_width=True, hide_index=True)
    except Exception as exc:
        st.error(f"Không thể đọc bảng {selected_table}: {exc}")


def _render_knowledge_hub(st: Any) -> None:
    """Browse indexed business rules and live RAG test."""
    st.subheader("🧠 Tra cứu Quy tắc Nghiệp vụ & Thử nghiệm RAG")
    st.caption("Khám phá các định nghĩa nghiệp vụ chuẩn được lập chỉ mục trong ChromaDB.")

    st.markdown("""
    Hệ thống RAG lập chỉ mục các file Markdown từ `docs/business_rules/`:
    - **Khách hàng mua lại (Repeat buyers):** Định danh bằng `customer_unique_id`, đếm `COUNT(DISTINCT order_id) > 1`.
    - **Giao hàng trễ (Late delivery):** Chỉ tính đơn `delivered` có `order_delivered_customer_date > order_estimated_delivery_date`.
    - **Doanh số vs Giá trị thanh toán:** `payment_value` là dòng tiền thanh toán; không được nhầm lẫn tùy tiện với doanh thu nếu không có yêu cầu cụ thể.
    - **Số lượng bán:** `order_items` lưu từng dòng hàng; lược đồ không có cột `quantity` rời nên không tự giả định.
    """)

    st.write("---")
    st.markdown("#### 🔍 Thử nghiệm Truy xuất Semantic RAG Live")
    test_query = st.text_input("Nhập câu hỏi hoặc khái niệm nghiệp vụ:", value="Khách hàng mua lại được tính như thế nào?")
    if st.button("Truy xuất quy tắc liên quan"):
        try:
            from src.rag.retriever import retrieve

            with st.spinner("Đang tìm kiếm vector trong ChromaDB..."):
                chunks = retrieve(test_query, n_results=3)

            if not chunks:
                st.info("Không tìm thấy đoạn quy tắc nào phù hợp.")
            else:
                for idx, chunk in enumerate(chunks, 1):
                    distance_str = f"(Độ lệch khoảng cách: {chunk.distance:.4f})" if chunk.distance is not None else ""
                    with st.expander(f"Top {idx}: {chunk.source} — {chunk.section} {distance_str}", expanded=(idx == 1)):
                        st.markdown(chunk.text)
        except Exception as exc:
            st.error(f"Lỗi truy xuất RAG: {exc}")


def _render_benchmark_hub(st: Any) -> None:
    """Display benchmark questions and A/B comparison rationale."""
    st.subheader("🔬 Benchmark & Cơ sở Khoa học Đánh giá")
    st.caption("Bộ 8 câu hỏi benchmark chuẩn hóa để đo lường năng lực Text-to-SQL và RAG.")

    if BENCHMARK_PATH.is_file():
        try:
            questions = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
            for q in questions:
                with st.expander(f"[{q.get('id')}] {q.get('question_vi')} ({q.get('difficulty', '').upper()})"):
                    st.write(f"**English:** {q.get('question_en')}")
                    st.markdown("**Reference SQL (Chuẩn đối chiếu):**")
                    st.code(q.get("reference_sql"), language="sql")
        except Exception as exc:
            st.error(f"Không đọc được benchmark: {exc}")
    else:
        st.warning("Không tìm thấy tệp benchmarks/questions.json.")


def main() -> None:
    """Create the interactive Streamlit page."""
    try:
        import streamlit as st
    except ImportError as error:
        raise RuntimeError(
            "Thiếu Streamlit. Hãy chạy: python -m pip install -r requirements.txt"
        ) from error

    st.set_page_config(
        page_title="TrustQuery AI — Safe Text-to-SQL Studio",
        page_icon="🔎",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _render_custom_css(st)

    # Sidebar Configuration
    with st.sidebar:
        st.markdown("### ⚙️ Cấu hình Hệ thống")

        # API Key & Model Configuration
        api_key_input = st.text_input(
            "Gemini API Key:",
            value=st.session_state.get("gemini_api_key", os.getenv("GEMINI_API_KEY", "")),
            type="password",
            help="Điền API key Google AI nếu chưa lưu trong file .env.",
        )
        if api_key_input:
            st.session_state.gemini_api_key = api_key_input
            os.environ["GEMINI_API_KEY"] = api_key_input

        model_options = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
        current_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        model_idx = model_options.index(current_model) if current_model in model_options else 0
        selected_model = st.selectbox("Mô hình AI:", model_options, index=model_idx)
        st.session_state.gemini_model = selected_model
        os.environ["GEMINI_MODEL"] = selected_model

        st.markdown("---")
        st.markdown("### 🛡️ Ranh giới An toàn")
        use_rag = st.toggle("Dùng quy tắc nghiệp vụ RAG", value=True, help="Bổ sung tri thức nghiệp vụ Olist vào prompt.")
        show_context = st.checkbox("Hiển thị đoạn trích RAG", value=True)

        st.caption("🔒 DuckDB Sandbox: `read_only=True` & `external_access=false`")
        st.caption("🛡️ AST Validation: SQLGlot kiểm tra SELECT / CTE duy nhất")

        st.markdown("---")
        if st.button("🗑️ Xóa lịch sử hội thoại", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.markdown("---")
        st.markdown("### 📚 Tài liệu & Bài báo")
        st.caption("• [Cơ sở khoa học (BIRD, DIN-SQL)](docs/scientific_foundations.md)")
        st.caption("• [Lược đồ Olist](docs/schema/olist_schema.md)")
        st.caption("• [Quy tắc RAG](docs/business_rules/rag_business_definitions.md)")

    # Main Header Card
    st.markdown(
        """
        <div class="tq-header-card">
            <h1 style="margin: 0; font-size: 2.2rem; font-weight: 700; color: #f8fafc;">
                🔎 TrustQuery AI <span style="font-size: 1.1rem; font-weight: 400; color: #94a3b8;">| Safe Text-to-SQL Studio</span>
            </h1>
            <p style="margin: 8px 0 16px 0; color: #cbd5e1; font-size: 1rem;">
                Hỏi đáp dữ liệu thương mại điện tử Olist bằng ngôn ngữ tự nhiên. Truy vấn chỉ thực thi sau khi vượt qua bộ xác thực AST và quy tắc an toàn.
            </p>
            <div>
                <span class="tq-status-badge badge-safe">🔒 DUCKDB SANDBOX: READ-ONLY</span>
                <span class="tq-status-badge badge-ast">🛡️ AST PARSER: SQLGLOT ALLOWLIST</span>
                <span class="tq-status-badge badge-rag">🧠 RAG: CHROMADB MULTILINGUAL</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Top-level Navigation Tabs
    main_tab1, main_tab2, main_tab3, main_tab4 = st.tabs(
        ["💬 AI Query Studio", "🗄️ Lược đồ Olist", "🧠 Quy tắc Nghiệp vụ & RAG", "🔬 Benchmark & Khoa học"]
    )

    with main_tab1:
        # Sample Question Presets (1-Click)
        st.markdown("**⚡ Câu hỏi mẫu nhanh (1-Click Presets):**")
        preset_cols = st.columns(len(_SAMPLE_PRESETS))
        clicked_question = None
        for i, preset in enumerate(_SAMPLE_PRESETS):
            with preset_cols[i]:
                if st.button(preset["label"], key=f"preset_{preset['id']}", use_container_width=True):
                    clicked_question = preset["question"]

        if "messages" not in st.session_state:
            st.session_state.messages = []

        # Render chat history
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                if message["role"] == "user":
                    st.write(message["content"])
                else:
                    render_outcome(st, message["outcome"], show_context=show_context)

        # Handle user input from chat or preset button
        chat_input_val = st.chat_input("Nhập câu hỏi của bạn (Ví dụ: Có bao nhiêu khách hàng đã mua lại?)...")
        question_to_process = clicked_question or chat_input_val

        if question_to_process:
            cleaned_question = question_to_process.strip()
            if cleaned_question:
                st.session_state.messages.append({"role": "user", "content": cleaned_question})
                with st.chat_message("user"):
                    st.write(cleaned_question)

                with st.chat_message("assistant"):
                    with st.spinner("Đang truy xuất RAG, tạo SQL và xác thực đa tầng an toàn..."):
                        outcome = answer_question(
                            cleaned_question,
                            use_rag=use_rag,
                            api_key=st.session_state.get("gemini_api_key"),
                            model=st.session_state.get("gemini_model"),
                        )
                    render_outcome(st, outcome, show_context=show_context)
                st.session_state.messages.append({"role": "assistant", "outcome": outcome})
                st.rerun()

    with main_tab2:
        _render_schema_explorer(st)

    with main_tab3:
        _render_knowledge_hub(st)

    with main_tab4:
        _render_benchmark_hub(st)


if __name__ == "__main__":
    main()
