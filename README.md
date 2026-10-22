# TrustQuery AI

TrustQuery AI là ứng dụng hỏi đáp dữ liệu Olist bằng ngôn ngữ tự nhiên. Hệ
thống tạo SQL DuckDB từ câu hỏi, bổ sung ngữ cảnh quy tắc nghiệp vụ bằng RAG,
rồi **xác thực SQL trước khi thực thi**. Mục tiêu là làm Text-to-SQL dễ kiểm
tra và an toàn hơn, không phải trao quyền truy vấn không giới hạn cho mô hình.

## Phạm vi và ranh giới tin cậy

- Đầu vào là câu hỏi về bộ dữ liệu thương mại điện tử Olist cục bộ.
- Gemini chỉ tạo *SQL đề xuất*; nó không có quyền kết nối trực tiếp tới
  DuckDB.
- Chỉ SQL qua validator mới được chạy trên DuckDB `read_only=True` với
  `enable_external_access=false`.
- Kết quả SQL hợp lệ vẫn cần được đánh giá theo ngữ nghĩa nghiệp vụ. Validator
  và RAG là lớp bảo vệ, không phải chứng minh rằng câu trả lời luôn đúng.

## Tính năng

| Thành phần | Vai trò |
| --- | --- |
| Text-to-SQL | Dùng Gemini để tạo đúng một câu SQL DuckDB kiểu `SELECT` hoặc `WITH ... SELECT`. |
| RAG quy tắc nghiệp vụ | Truy xuất các định nghĩa như khách mua lại, giao trễ và giá trị thanh toán từ Markdown được quản trị trong Git. |
| SQL validator | Kiểm tra cú pháp, schema, an toàn và một số bẫy nghiệp vụ trước khi truy vấn chạy. |
| DuckDB an toàn | Chỉ cho phép bảng Olist cục bộ, kết nối chỉ đọc và chặn truy cập tệp, URL hoặc nguồn ngoài. |
| Streamlit UI | Hiển thị câu hỏi, SQL, trạng thái kiểm tra, cảnh báo, bảng kết quả và biểu đồ phù hợp. |
| Benchmark A/B | So sánh cùng tập câu hỏi ở chế độ schema-only và schema + RAG. |

## Kiến trúc

```text
Câu hỏi người dùng
       │
       ├── (tuỳ chọn) RAG: ChromaDB + business rules Markdown
       │
       ▼
Gemini tạo SQL đề xuất cùng schema Olist
       │
       ▼
SQL validator
  ├── cú pháp / đúng một statement
  ├── allowlist bảng và cột
  ├── chặn DDL, DML, nguồn ngoài
  └── quy tắc nghiệp vụ
       │
       ▼
DuckDB chỉ đọc, external access bị tắt
       │
       ▼
Streamlit: SQL + trạng thái + kết quả + biểu đồ
```

## Yêu cầu

- Python 3.10 trở lên.
- Chín tệp CSV Olist gốc (không đi kèm repository) đặt trong `data/raw/`.
- Tài khoản Google AI có API key và tên model khả dụng nếu muốn tạo SQL bằng
  Gemini.
- Kết nối Internet ở lần đầu lập chỉ mục RAG để tải model embedding, trừ khi
  model đã được cache cục bộ.

Các dữ liệu cục bộ, database, ChromaDB, file `.env` và báo cáo benchmark sinh
ra đều bị bỏ qua khỏi Git.

## Cài đặt nhanh trên Windows PowerShell

```powershell
git clone <repository-url>
cd TrustQuery-AI

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Copy-Item .env.example .env
```

Mở `.env` và điền cấu hình của bạn:

```dotenv
GEMINI_API_KEY=your_key
GEMINI_MODEL=your_available_model
```

Không commit file `.env`. Nếu dùng macOS/Linux, kích hoạt môi trường bằng
`source .venv/bin/activate`.

## Chuẩn bị dữ liệu Olist

Tạo thư mục `data/raw/` và đặt đúng các tệp sau vào đó:

| Bảng DuckDB | Tệp CSV cần có |
| --- | --- |
| `customers` | `olist_customers_dataset.csv` |
| `geolocation` | `olist_geolocation_dataset.csv` |
| `order_items` | `olist_order_items_dataset.csv` |
| `order_payments` | `olist_order_payments_dataset.csv` |
| `order_reviews` | `olist_order_reviews_dataset.csv` |
| `orders` | `olist_orders_dataset.csv` |
| `products` | `olist_products_dataset.csv` |
| `sellers` | `olist_sellers_dataset.csv` |
| `category_translation` | `product_category_name_translation.csv` |

Kiểm tra dữ liệu trước khi nạp:

```powershell
python -m src.database.inspect_data
```

Nạp CSV vào DuckDB cục bộ tại `data/database/olist.duckdb`:

```powershell
python -m src.database.load_data
python -m src.database.smoke_database
```

`smoke_database` chỉ in metadata và vài truy vấn mẫu; nó không thay đổi dữ
liệu. Đọc thêm về grain, quan hệ và các bẫy JOIN trong
[docs/schema/olist_schema.md](docs/schema/olist_schema.md).

## Lập chỉ mục RAG

Nguồn tri thức là các file Markdown trong `docs/business_rules/`. Lệnh sau
tách theo heading, tạo embedding đa ngôn ngữ và lưu ChromaDB cục bộ ở
`data/chroma/`:

```powershell
python -m src.rag.build_index
```

Kiểm tra retrieval độc lập với Gemini:

```powershell
python -m src.rag.retriever "Có bao nhiêu khách hàng đã mua lại?"
```

Kết quả nên nêu rõ khách mua lại được nhận diện bằng
`customer_unique_id`, không chỉ `customer_id`. Nếu đổi model embedding, hãy
lập lại index hoặc truyền cùng tên model cho retriever/Text-to-SQL.

## Chạy giao diện

```powershell
python -m streamlit run app.py
```

Trong sidebar:

- Bật **Dùng quy tắc nghiệp vụ RAG** khi index đã được xây.
- Tắt RAG để chạy chế độ chỉ schema (schema-only).
- Bật **Hiển thị ngữ cảnh RAG** khi cần kiểm tra các đoạn tài liệu đã được đưa
  vào prompt.

Mỗi phản hồi cho thấy SQL đã tạo, kết quả validator, cảnh báo nghiệp vụ, dữ
liệu trả về và biểu đồ cột khi kết quả có cấu trúc metric phù hợp. Ứng dụng
chỉ hiển thị tối đa 1.000 dòng; đây là giới hạn hiển thị, không phải giới hạn
chi phí thực thi truy vấn.

## Validator hoạt động như thế nào

`src/validation/sql_validator.py` áp dụng các lớp sau theo thứ tự:

1. **Cú pháp:** SQLGlot phải phân tích được đúng một truy vấn DuckDB dạng
   `SELECT` hoặc `WITH ... SELECT`.
2. **An toàn:** từ chối nhiều statement, DDL/DML, transaction, lệnh cấu hình,
   table function đọc tệp/URL và truy cập catalog/schema ngoài phạm vi Olist.
3. **Schema:** chỉ chấp nhận bảng có trong `src.database.load_data.TABLES`.
   Bảng, cột, alias và output CTE/derived table được DuckDB `EXPLAIN` kiểm tra
   trên kết nối chỉ đọc.
4. **Nghiệp vụ:** từ chối phép `SUM` có nguy cơ nhân số liệu khi JOIN trực tiếp
   `order_items` với `order_payments`; cảnh báo các mẫu như dùng `customer_id`
   cho khách duy nhất/mua lại hoặc suy ra đơn đã giao chỉ từ thời điểm giao.

Kết nối truy vấn dùng `connect_read_only()` ở
`src/database/read_only.py`. Ngoài `read_only=True`, nó đặt
`enable_external_access=false` để SQL sinh ra không thể đọc file, URL hoặc
gắn nguồn dữ liệu ngoài.

## Dùng các lệnh dòng lệnh

### Sinh SQL không dùng RAG

Lệnh này chỉ in SQL, không thực thi:

```powershell
python -m src.llm.text_to_sql "Có bao nhiêu đơn hàng đã giao trong năm 2018?"
```

### Sinh SQL có RAG

```powershell
python -m src.llm.text_to_sql --use-rag "Có bao nhiêu khách hàng đã mua lại?"
```

### So sánh schema-only và RAG

Lệnh dưới đây gọi Gemini cho từng câu hỏi benchmark, do đó có thể tiêu tốn hạn
mức API. Báo cáo JSON được tạo tại `benchmarks/results/rag_comparison.json`
và bị bỏ qua khỏi Git.

```powershell
python -m src.evaluation.compare_rag
python -m src.evaluation.compare_rag --question-id Q006
```

Không đánh giá A/B bằng so sánh chuỗi SQL. Hãy đối chiếu ngữ nghĩa, grain,
phạm vi lọc và kết quả thực tế với `reference_sql`.

## Kiểm thử

Chạy toàn bộ test:

```powershell
python -m pytest -q
```

Các test có marker `requires_olist_database` là integration test và cần
`data/database/olist.duckdb`. Trên clone mới chưa nạp dữ liệu, chúng sẽ được
skip thay vì làm suite thất bại. Sau khi nạp Olist, suite kiểm tra schema,
validator, UI executor, RAG chunking/retrieval và benchmark reference SQL.
Các test không gọi Gemini thật.

## Cấu trúc dự án

```text
app.py                         # Giao diện Streamlit
benchmarks/questions.json      # Câu hỏi và SQL tham chiếu
docs/
  business_rules/              # Nguồn tri thức RAG và quy tắc nghiệp vụ
  schema/                      # Grain, bảng, quan hệ và rủi ro JOIN
src/
  database/                    # Nạp, kiểm tra và kết nối DuckDB an toàn
  evaluation/                  # So sánh A/B schema-only và RAG
  llm/                         # Prompt và Gemini Text-to-SQL
  rag/                         # Xây index và truy xuất ChromaDB
  validation/                  # Safety guard và full SQL validator
tests/                         # Unit và integration tests
```

## Giới hạn hiện tại

- Đây là prototype local một người dùng, chưa có policy timeout, memory limit
  hoặc quota/concurrency cho truy vấn tốn tài nguyên.
- `LIMIT` ở UI chỉ giảm số dòng trả về sau khi truy vấn chạy; không ngăn được
  một phép JOIN/aggregate/ORDER BY vốn đã tốn chi phí.
- Validator chỉ mã hoá một số quy tắc nghiệp vụ đã biết. RAG có thể giúp mô
  hình chọn định nghĩa đúng hơn nhưng không thay thế benchmark hoặc review.
- “Giá trị thanh toán” là `SUM(payment_value)` theo phạm vi câu hỏi; không tự
  động đồng nghĩa với “doanh thu”.
- Raw data và nội dung API key thuộc trách nhiệm vận hành của người dùng. Hãy
  tuân thủ giấy phép dữ liệu Olist và không đưa thông tin bí mật vào prompt.

## Khắc phục sự cố

| Triệu chứng | Cách xử lý |
| --- | --- |
| Báo chưa có database | Kiểm tra chín CSV trong `data/raw/`, rồi chạy `python -m src.database.load_data`. |
| Báo chưa có index RAG | Chạy `python -m src.rag.build_index` hoặc tắt RAG trong sidebar. |
| Báo model embedding không khớp | Lập lại index với model hiện tại hoặc dùng cùng tên model khi truy xuất. |
| Thiếu `GEMINI_API_KEY`/`GEMINI_MODEL` | Sao chép `.env.example` thành `.env` và điền cả hai biến. |
| Test integration bị skip | Đây là hành vi mong đợi khi database cục bộ chưa được nạp. |
| SQL bị từ chối | Đọc lỗi/cảnh báo ở UI; kiểm tra schema, grain, tên cột và quy tắc nghiệp vụ trước khi thử lại. |

## Tài liệu bổ sung

- [Schema Olist](docs/schema/olist_schema.md)
- [Quy tắc nghiệp vụ ban đầu](docs/business_rules/initial_rules.md)
- [Định nghĩa nghiệp vụ dùng cho RAG](docs/business_rules/rag_business_definitions.md)
- [Thiết kế và đánh giá RAG](docs/rag.md)
- [Validator và giao diện](docs/validator_ui.md)
