# Benchmark TrustQuery AI

`questions.json` chứa 120 bài toán nghiệp vụ độc lập cho đánh giá Text-to-SQL.
Mỗi bài toán có `question_vi` và `question_en`; công cụ đánh giá chạy chúng như
hai ca riêng biệt, vì vậy một lần đánh giá đầy đủ có 240 ca ngôn ngữ.

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
  thông tin cần thiếu hoặc lý do phải từ chối. Chúng cần chấm thủ công.

## Chạy và đọc kết quả

```powershell
# Luôn kiểm tra một bài toán trước (tạo 2 ca: vi/en)
python -m src.evaluation.compare_rag --question-id Q006

# Chạy toàn bộ 120 bài toán khi API, RAG và chi phí đã sẵn sàng
python -m src.evaluation.compare_rag
```

Trình đánh giá tạo hai SQL ứng viên cho mỗi ca: `schema_only` và `rag`.
`rag_validation` dùng đúng SQL ứng viên của `rag`, rồi biểu diễn kết quả sau
Validator; nó không tạo một lượt gọi mô hình thứ ba. SQL chỉ được thực thi khi
đã vượt qua Validator và luôn chạy qua DuckDB read-only, external access bị
tắt.

Với các ca `query`, báo cáo gồm:

- `execution_accuracy`: kết quả thực thi khớp SQL tham chiếu / toàn bộ ca
  `query`; ca lỗi hoặc bị từ chối Validator tính là không khớp.
- `valid_sql_rate`: SQL được Validator chấp thuận / toàn bộ ca `query`.
- `false_acceptance_rate`: SQL được chấp thuận nhưng cho kết quả khác chuẩn /
  toàn bộ ca `query`.

Các ca `clarify_or_refuse` được để riêng khỏi các tỷ lệ này và yêu cầu người
đánh giá xác nhận mô hình đã từ chối hoặc hỏi lại một cách phù hợp.
