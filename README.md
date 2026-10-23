# TrustQuery AI 🔎

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![DuckDB](https://img.shields.io/badge/DuckDB-1.1%2B-yellow.svg)](https://duckdb.org/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-Vector_Store-orange.svg)](https://www.trychroma.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-UI_Studio-FF4B4B.svg)](https://streamlit.io/)
[![Safety](https://img.shields.io/badge/Security-Read--Only_Sandbox-success.svg)](#ranh-giới-tin-cậy-và-an-toàn-đa-tầng)

**TrustQuery AI** là ứng dụng hỏi đáp dữ liệu thương mại điện tử Olist bằng ngôn ngữ tự nhiên. Hệ thống kết hợp mô hình ngôn ngữ lớn (Gemini), công nghệ **RAG quy tắc nghiệp vụ** (Retrieval-Augmented Generation với ChromaDB) và **bộ xác thực SQL AST đa tầng** (SQLGlot) để đảm bảo mọi câu truy vấn được kiểm tra an toàn và đúng đắn trước khi thực thi trên môi trường sandbox DuckDB.

---

## 📑 Mục lục

1. [Cơ sở khoa học & Nghiên cứu liên quan](#-cơ-sở-khoa-học--nghiên-cứu-liên-quan)
2. [Ranh giới tin cậy và An toàn đa tầng](#-ranh-giới-tin-cậy-và-an-toàn-đa-tầng)
3. [Kiến trúc hệ thống](#-kiến-trúc-hệ-thống)
4. [Các tính năng nổi bật](#-các-tính-năng-nổi-bật)
5. [Cài đặt và Khởi chạy](#-cài-đặt-và-khởi-chạy)
6. [Sử dụng Giao diện Streamlit](#-sử-dụng-giao-diện-streamlit)
7. [Dùng các lệnh dòng lệnh (CLI)](#-dùng-các-lệnh-dòng-lệnh-cli)
8. [Kiểm thử tự động (Testing)](#-kiểm-thử-tự-động-testing)
9. [Cấu trúc thư mục dự án](#-cấu-trúc-thư-mục-dự-án)
10. [Tài liệu tham khảo](#-tài-liệu-tham-khảo)

---

## 🔬 Cơ sở khoa học & Nghiên cứu liên quan

TrustQuery AI được xây dựng trên nền tảng các công trình nghiên cứu học thuật hàng đầu về Text-to-SQL và An toàn AI:

1. **BIRD Benchmark (NeurIPS 2023)**: *Can LLM Already Serve as A Database Interface? A BIg Bench for Large-Scale Database Grounded Text-to-SQLs* ([arXiv:2305.03111](https://arxiv.org/abs/2305.03111)).
   - **Vấn đề chỉ ra:** LLM truyền thống đạt độ chính xác thấp trên dữ liệu doanh nghiệp vì thiếu hiểu biết về giá trị thực tế và quy ước ngầm định (External Knowledge Grounding).
   - **Giải pháp trong TrustQuery:** Sử dụng RAG vector hóa các quy tắc nghiệp vụ Olist (như phân biệt `customer_id` vs `customer_unique_id`, logic giao hàng trễ) để cung cấp ngữ cảnh chính xác cho mô hình.

2. **DIN-SQL (NeurIPS 2023)**: *DIN-SQL: Decomposed In-Context Learning of Text-to-SQL with Self-Correction* ([arXiv:2304.11015](https://arxiv.org/abs/2304.11015)).
   - **Tư tưởng áp dụng:** Phân rã quá trình sinh SQL thành các khâu độc lập: Phân tích ngữ cảnh ➔ Sinh SQL ứng viên ➔ Kiểm tra cú pháp/schema ➔ Phản hồi cảnh báo nghiệp vụ.

3. **Spider Benchmark (EMNLP 2018)**: *Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing and Text-to-SQL* ([arXiv:1809.08887](https://arxiv.org/abs/1809.08887)).
   - **Phương pháp luận:** Chuẩn hóa quy trình đánh giá dựa trên đối chiếu kết quả thực thi bảng (Execution Output) thay vì so sánh chuỗi ký tự SQL thô sơ.

4. **Domain Database Knowledge Injection (arXiv:2409.15907, 2024)** & **RAG Content Balancing (2024)**:
   - Chứng minh kỹ thuật phân mảnh theo tiêu đề ngữ nghĩa (Heading-based chunking) giúp loại bỏ nhiễu ngữ cảnh, tối ưu hóa kích thước prompt và triệt tiêu ảo giác (hallucination).

> Chi tiết bài phân tích và đối chiếu lý thuyết được trình bày tại [docs/scientific_foundations.md](docs/scientific_foundations.md).

---

## 🛡️ Ranh giới tin cậy và An toàn đa tầng

```text
               [ Câu hỏi người dùng ]
                         │
                         ▼
             [ RAG: Truy xuất quy tắc ]
                         │
                         ▼
        [ Gemini: Sinh SQL đề xuất (Zero DB Privileges) ]
                         │
 ┌───────────────────────┴───────────────────────┐
 │             BỘ XÁC THỰC SQL ĐA TẦNG           │
 │  1. AST Syntax Check: Chỉ 1 câu SELECT / CTE   │
 │  2. Allowlist Schema: Bảng và cột Olist hợp lệ │
 │  3. Safety Guard: Chặn DDL, DML, File, URL    │
 │  4. Business Guard: Chặn phép JOIN nhân số liệu│
 └───────────────────────┬───────────────────────┘
                         │ (Chỉ khi HỢP LỆ)
                         ▼
          [ DuckDB Sandbox C++ Isolated ]
           • read_only = True
           • enable_external_access = False
           • memory_limit = 512MB
                         │
                         ▼
            [ Giao diện trực quan hóa ]
```

- **Quyền hạn mô hình = 0:** Gemini không có bất kỳ kết nối nào tới cơ sở dữ liệu DuckDB. Mô hình chỉ sinh văn bản SQL ứng viên.
- **Không tin tưởng SQL đầu ra (Zero-Trust SQL):** Mọi câu truy vấn đều phải vượt qua bộ kiểm tra AST tĩnh trước khi được chuyển đến database driver.
- **Cách ly cấp độ C++ Engine:** DuckDB được cấu hình chặn toàn bộ truy cập hệ thống file cục bộ, network request hoặc nạp extension ngoài (`enable_external_access=false`), đồng thời giới hạn bộ nhớ truy vấn. Giao diện chỉ tải tối đa 1.000 dòng kết quả để bảo vệ phiên làm việc.

---

## ✨ Các tính năng nổi bật

| Tính năng | Mô tả |
| :--- | :--- |
| **💬 AI Query Studio** | Giao diện hỏi đáp ngôn ngữ tự nhiên thông minh, hỗ trợ cả câu hỏi tiếng Việt và tiếng Anh. |
| **⚡ Câu hỏi mẫu 1-Click** | Bộ câu hỏi gợi ý từ benchmark (Doanh thu, Top danh mục, Khách mua lại, Đơn giao trễ) giúp kiểm tra tức thì. |
| **🛡️ Bộ xác thực SQL trực quan** | Hiển thị chi tiết từng bước kiểm tra an toàn, phân tích cú pháp AST và kế hoạch thực thi (`EXPLAIN`). |
| **🗄️ Olist Schema Explorer** | Xem trực tiếp 9 bảng dữ liệu Olist, số lượng dòng, kiểu dữ liệu từng cột và xem trước dữ liệu mẫu mà không cần viết SQL. |
| **🧠 Business Knowledge Base** | Tra cứu từ điển quy tắc nghiệp vụ đã lập chỉ mục và công cụ Semantic Search test RAG trực tiếp trên giao diện. |
| **📈 Trực quan hóa tự động** | Tự động nhận diện cấu trúc metric để vẽ biểu đồ cột, bảng phân tích và thẻ KPI tóm tắt. |
| **📥 Xuất dữ liệu CSV** | Hỗ trợ tải kết quả truy vấn về máy với 1 cú click chuột. |
| **🔬 Chế độ Benchmark A/B** | Dễ dàng bật/tắt RAG để so sánh hiệu quả giữa chế độ có và không có quy tắc nghiệp vụ. |

---

## 🚀 Cài đặt và Khởi chạy

### 1. Yêu cầu hệ thống
- Python 3.10 trở lên (khuyến nghị Python 3.11 hoặc 3.12).
- Khuyến nghị sử dụng Windows PowerShell hoặc Terminal Linux/macOS.

### 2. Thiết lập môi trường

```powershell
# Clone repository
git clone <repository-url>
cd TrustQuery-AI

# Tạo và kích hoạt virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Cài đặt thư viện
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

*(Trên Linux/macOS, kích hoạt môi trường bằng `source .venv/bin/activate`)*

### 3. Cấu hình API Key

Sao chép `.env.example` thành `.env` nếu bạn muốn nạp sẵn API key:
```powershell
Copy-Item .env.example .env
```
Điền thông tin Google AI Studio của bạn:
```dotenv
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash-lite
```
`app.py` nạp tệp `.env` khi khởi động. Bạn cũng có thể nhập API key trực tiếp
trên sidebar giao diện Streamlit như một ghi đè tạm thời cho phiên hiện tại;
khóa cấu hình sẵn không được điền lại vào ô nhập. Khi chưa cấu hình key, ứng
dụng báo lỗi cấu hình thay vì dùng `reference_sql` của benchmark làm câu trả
lời demo.

### 4. Dữ liệu và Chỉ mục RAG
Cơ sở dữ liệu DuckDB (`data/database/olist.duckdb`) và chỉ mục vector ChromaDB (`data/chroma/`) đã được khởi tạo sẵn trong workspace. Nếu cần tái tạo từ đầu:
```powershell
# Nạp 9 CSV Olist vào DuckDB
python -m src.database.load_data

# Xây dựng lại chỉ mục vector RAG
python -m src.rag.build_index
```

---

## 🖥️ Sử dụng Giao diện Streamlit

Khởi chạy ứng dụng giao diện cao cấp:

```powershell
python -m streamlit run app.py
```

Ứng dụng sẽ tự động mở trên trình duyệt tại `http://localhost:8501`:
1. **AI Query Studio:** Nhập câu hỏi tự do hoặc bấm vào các câu hỏi mẫu gợi ý trên thanh Presets để trải nghiệm ngay.
2. **Kiểm tra SQL & Kế hoạch:** Mở các tab phân tích để xem SQL đã tạo, kiểm tra AST, kế hoạch thực thi `EXPLAIN`, và đoạn trích RAG đã sử dụng.
3. **Schema Explorer:** Khám phá cấu trúc 9 bảng dữ liệu Olist và dữ liệu thực tế.
4. **Knowledge Base:** Tra cứu và thử nghiệm tìm kiếm vector các quy tắc nghiệp vụ.

---

## ⌨️ Dùng các lệnh dòng lệnh (CLI)

### 1. Kiểm tra RAG Retriever độc lập
```powershell
python -m src.rag.retriever "Có bao nhiêu khách hàng đã mua lại?"
```

### 2. Sinh SQL qua dòng lệnh (Không thực thi)
```powershell
# Chế độ Schema-only (Baseline)
python -m src.llm.text_to_sql "Top 5 danh mục bán chạy nhất?"

# Chế độ RAG (Có quy tắc nghiệp vụ)
python -m src.llm.text_to_sql --use-rag "Có bao nhiêu khách hàng đã mua lại?"
```

### 3. Chạy Benchmark So sánh A/B
```powershell
# Kiểm tra trước một bài toán (hai biến thể tiếng Việt/Anh)
python -m src.evaluation.compare_rag --question-id Q006

# Mặc định chỉ chạy tập development
python -m src.evaluation.compare_rag

# Chạy tập test giữ lại hoặc toàn bộ benchmark khi đã sẵn sàng
python -m src.evaluation.compare_rag --split test
python -m src.evaluation.compare_rag --split all
```
Kết quả có `execution_accuracy`, `valid_sql_rate` và `false_acceptance_rate`
được lưu tại `benchmarks/results/rag_comparison.json`. Xem
[`benchmarks/README.md`](benchmarks/README.md) để biết cấu trúc bộ dữ liệu và
quy tắc đánh giá. Chỉ `schema_only` và `rag` là hai phương pháp LLM được so
sánh; `rag_validation_gate` là góc nhìn xác thực trên chính ứng viên RAG, không
phải một lượt gọi mô hình thứ ba. Các ca `clarify_or_refuse` được giữ để review
thủ công và không tạo SQL hay được chấm bằng các chỉ số Text-to-SQL.

---

## 🧪 Kiểm thử tự động (Testing)

Dự án có bộ kiểm thử tự động toàn diện bao quát tính đúng đắn của schema, validator AST, retriever vector và query pipeline:

```powershell
python -m pytest -q
```

---

## 📁 Cấu trúc thư mục dự án

```text
TrustQuery-AI/
├── app.py                         # Giao diện chính Streamlit Studio
├── benchmarks/
│   ├── questions.json             # 120 bài toán benchmark song ngữ
│   ├── README.md                  # Hợp đồng split, nhãn và thước đo benchmark
│   └── results/                   # Kết quả đánh giá A/B
├── data/
│   ├── chroma/                    # Vector store ChromaDB cục bộ
│   ├── database/olist.duckdb      # Cơ sở dữ liệu DuckDB Olist (read-only)
│   └── raw/                       # 9 tệp dữ liệu CSV gốc Olist
├── docs/
│   ├── business_rules/            # Quy tắc nghiệp vụ dùng cho RAG (.md)
│   ├── schema/olist_schema.md     # Tài liệu lược đồ bảng và rủi ro JOIN
│   ├── scientific_foundations.md  # Cơ sở khoa học & các bài báo liên quan
│   ├── rag.md                     # Tài liệu kiến trúc và đánh giá RAG
│   └── validator_ui.md            # Tài liệu cơ chế xác thực SQL
├── src/
│   ├── console.py                 # Tiện ích cấu hình UTF-8 console
│   ├── database/                  # Quản lý kết nối DuckDB và nạp dữ liệu
│   ├── evaluation/                # Module so sánh A/B benchmark
│   ├── llm/                       # Sinh SQL và cấu trúc prompt Gemini
│   ├── rag/                       # Bộ lập chỉ mục và truy xuất ChromaDB
│   └── validation/                # Bộ kiểm tra an toàn AST và logic nghiệp vụ
└── tests/                         # Suite kiểm thử tự động Pytest
```

---

## 📚 Tài liệu tham khảo

- [Cơ sở khoa học và Nghiên cứu liên quan](docs/scientific_foundations.md)
- [Chi tiết Lược đồ Olist](docs/schema/olist_schema.md)
- [Định nghĩa Nghiệp vụ cho RAG](docs/business_rules/rag_business_definitions.md)
- [Cơ chế RAG và Đánh giá A/B](docs/rag.md)
- [Bộ xác thực SQL và Giao diện](docs/validator_ui.md)
