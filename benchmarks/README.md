# Benchmark TrustQuery AI

`questions.json` chứa 120 bài toán nghiệp vụ độc lập cho đánh giá Text-to-SQL.
Mỗi bài toán có `question_vi` và `question_en`; công cụ đánh giá mở rộng chúng
thành hai ca riêng biệt. Lệnh mặc định chỉ chạy tập `development`; dùng
`--split all` để chạy toàn bộ 240 ca ngôn ngữ.

## Phân bổ

| Nhóm | Số bài toán | Mục đích |
| --- | ---: | --- |
| `basic_sql` | 20 | Chọn bảng/cột, đếm và truy vấn đơn giản |
| `filters_time` | 20 | Lọc, trạng thái và phạm vi thời gian |
| `joins` | 20 | Hiểu các quan hệ bảng và grain |
| `aggregation` | 20 | Tổng hợp, nhóm, trung bình và Top-N |
| `complex_business` | 25 | Khách mua lại, giao hàng trễ và rủi ro fan-out JOIN |
| `ambiguous_out_of_scope` | 15 | Từ chối hoặc yêu cầu làm rõ thay vì bịa dữ liệu |

Có 30 bài `development` để kiểm tra prompt/quy tắc trong quá trình phát triển
và 90 bài `test` giữ lại cho báo cáo cuối. Không đưa SQL tham chiếu của tập
`test` vào nguồn RAG.

## Hợp đồng mỗi bản ghi

- `id`: mã bài toán ổn định, ví dụ `Q006`.
- `question_vi`, `question_en`: hai prompt tương đương về ngữ nghĩa.
- `difficulty`, `category`, `split`: nhãn phân tầng khi phân tích kết quả.
- `expected_outcome`: `query` hoặc `clarify_or_refuse`.
- Các ca `query` có `reference_sql` đã được Validator và DuckDB read-only xác
  nhận. Đây chỉ là chuẩn đánh giá, không được dùng làm câu trả lời demo.
- Các ca `clarify_or_refuse` không có `reference_sql`; `evaluation_note` mô tả
  thông tin cần thiếu hoặc lý do phải từ chối. Chúng cần chấm thủ công và không
  kích hoạt truy xuất RAG, sinh SQL, xác thực SQL hoặc thực thi database.

## Chạy và đọc kết quả

```powershell
# Luôn kiểm tra một bài toán trước (tạo 2 ca: vi/en)
python -m src.evaluation.compare_rag --question-id Q006

# Mặc định: chạy 30 bài thuộc tập development
python -m src.evaluation.compare_rag

# Chạy 90 bài test giữ lại hoặc toàn bộ 120 bài khi API, RAG và chi phí đã sẵn sàng
python -m src.evaluation.compare_rag --split test
python -m src.evaluation.compare_rag --split all
```

`--question-id` có ưu tiên cao hơn `--split`, nên có thể chạy lại một bài test
cụ thể mà không cần đổi split.

Trình đánh giá tạo hai SQL ứng viên cho mỗi ca: `schema_only` và `rag`.
`rag_validation_gate` dùng đúng SQL ứng viên của `rag`, rồi biểu diễn trạng
thái sau Validator; đây là một view xác thực, không phải phương pháp LLM độc
lập và không tạo lượt gọi mô hình thứ ba. SQL chỉ được thực thi khi đã vượt qua
Validator và luôn chạy qua DuckDB read-only, external access bị tắt. Kết quả
vượt quá giới hạn so sánh được báo là `not_comparable`, thay vì chỉ so sánh một
phần kết quả.

Với các ca `query`, báo cáo gồm:

- `execution_accuracy`: kết quả thực thi khớp SQL tham chiếu / toàn bộ ca
  `query`; ca lỗi hoặc bị từ chối Validator tính là không khớp.
- `valid_sql_rate`: SQL được Validator chấp thuận / toàn bộ ca `query`.
- `false_acceptance_rate`: SQL được chấp thuận nhưng cho kết quả khác chuẩn /
  toàn bộ ca `query`.

Các ca `clarify_or_refuse` được để riêng khỏi các tỷ lệ này. Báo cáo chỉ đánh
dấu chúng cần review thủ công; cần một bộ đánh giá hội thoại/từ chối riêng để
xác nhận mô hình đã từ chối hoặc hỏi lại một cách phù hợp.
