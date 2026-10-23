# RAG cho quy tắc nghiệp vụ

RAG bổ sung định nghĩa nghiệp vụ đã được quản trị vào Text-to-SQL. Nó không
thay thế schema và không tự chạy SQL. Chế độ mặc định vẫn là **A**
(schema-only) để có thể so sánh công bằng với **B** (schema + RAG).

> Cơ sở lý thuyết và các nghiên cứu khoa học liên quan (BIRD Benchmark, Knowledge Injection, RAG Content Balancing) được trình bày chi tiết tại [docs/scientific_foundations.md](scientific_foundations.md).


## Thành phần

- `docs/business_rules/*.md`: nguồn tri thức đã được kiểm soát phiên bản.
- `src/rag/build_index.py`: tách Markdown theo tiêu đề cấp 2, tạo embedding
  đa ngôn ngữ bằng `sentence-transformers`, rồi ghi vector vào ChromaDB cục
  bộ tại `data/chroma/`.
- `src/rag/retriever.py`: nhúng câu hỏi, lấy các đoạn gần nhất và định dạng
  chúng kèm nguồn/mục để đưa vào prompt.
- `src/llm/text_to_sql.py --use-rag`: phiên bản B của Text-to-SQL.

Chỉ mục là dữ liệu sinh ra, đã bị bỏ qua khỏi Git. Mỗi lần lập chỉ mục, bộ sưu
tập `olist_business_rules` được tạo lại từ toàn bộ Markdown hiện tại để không
giữ đoạn cũ sau khi tài liệu đã bị sửa hoặc xóa.

## Cài đặt và lập chỉ mục

Từ thư mục gốc dự án:

```powershell
python -m pip install -r requirements.txt
python -m src.rag.build_index
```

Lần đầu `sentence-transformers` tải model
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Model này phù
hợp với cả câu hỏi benchmark tiếng Việt lẫn tiếng Anh. Việc lập chỉ mục không
đọc hoặc sửa DuckDB/Olist raw data.

Sau khi lập chỉ mục thành công, retriever chỉ dùng bản model đã cache cục bộ;
việc đặt câu hỏi không cần gọi Hugging Face lần nữa.

Khi model đã cache mà bạn cần lập lại chỉ mục ngoài mạng, thêm
`--local-files-only` vào lệnh `build_index`.

Nếu thay `--model` lúc lập chỉ mục, hãy truyền đúng model đó qua `--model` cho
retriever hoặc `--rag-model` cho Text-to-SQL/đánh giá. Chỉ mục mới lưu tên
model để phát hiện nhầm model thay vì truy vấn với vector không tương thích.

Kiểm tra truy xuất trước khi gọi LLM:

```powershell
python -m src.rag.retriever "Có bao nhiêu khách hàng đã mua lại?"
```

Kết quả cần có đoạn nói rằng khách mua lại được nhóm theo
`customer_unique_id`, không phải chỉ `customer_id`.

## Chạy hai phiên bản Text-to-SQL

```powershell
# A — schema cố định, đúng hành vi trước khi thêm RAG
python -m src.llm.text_to_sql "Có bao nhiêu khách hàng đã mua lại?"

# B — schema cộng với các quy tắc nghiệp vụ truy xuất được
python -m src.llm.text_to_sql --use-rag "Có bao nhiêu khách hàng đã mua lại?"
```

Cả hai lệnh chỉ in SQL đề xuất. Chúng vẫn cần được kiểm tra bởi con người và
không tự thực thi câu lệnh lên database.

## Đánh giá benchmark song ngữ

Sau khi tạo chỉ mục, cấu hình `.env` với Gemini và xác nhận database đã được
nạp, chạy:

```powershell
python -m src.evaluation.compare_rag
```

Lệnh dùng chung `benchmarks/questions.json` cho cả hai phiên bản và tạo
`benchmarks/results/rag_comparison.json`. Mỗi bài toán có một prompt tiếng
Việt và một prompt tiếng Anh. Với 120 bài toán, lần chạy đầy đủ tạo 240 ca
ngôn ngữ và 480 lượt sinh SQL: schema-only và RAG cho từng ca.

Mọi SQL ứng viên phải qua Validator trước khi được thực thi bằng kết nối
DuckDB chỉ đọc. Báo cáo so sánh kết quả thực thi với SQL tham chiếu cho các ca
`query`, đồng thời tổng hợp `execution_accuracy`, `valid_sql_rate`, và
`false_acceptance_rate`. Biến thể `rag_validation` tái sử dụng SQL của RAG,
không gọi mô hình lần thứ ba.

Các ca `clarify_or_refuse` không có `reference_sql`, không được thực thi, và
được ghi rõ là cần đánh giá thủ công về chất lượng từ chối/yêu cầu làm rõ. Chi
tiết hợp đồng benchmark nằm ở [benchmarks/README.md](../benchmarks/README.md).

Nên chạy một bài toán trước, ví dụ:

```powershell
python -m src.evaluation.compare_rag --question-id Q006
```
