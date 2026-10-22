# SQL Validator và giao diện TrustQuery AI

Ứng dụng không thực thi trực tiếp SQL do mô hình tạo. Mỗi candidate phải qua
validator trước, sau đó mới được chạy bằng kết nối DuckDB `read_only=True`.

## Các lớp kiểm tra

1. **Cú pháp:** SQLGlot phân tích đúng một câu truy vấn DuckDB kiểu `SELECT`
   hoặc CTE kết thúc bằng `SELECT`.
2. **An toàn:** từ chối DDL/DML, transaction, nhiều câu lệnh, lệnh cấu hình và
   hàm/bảng đọc tệp, URL, nguồn dữ liệu ngoài.
3. **Schema:** chỉ cho phép bảng Olist cục bộ. Sau đó DuckDB `EXPLAIN` trên
   kết nối chỉ đọc kiểm tra bảng, cột, alias và CTE thực sự tồn tại.
4. **Nghiệp vụ:** từ chối phép tổng hợp có nguy cơ nhân giá trị khi nối đồng
   thời `order_items` và `order_payments`; đồng thời cảnh báo các mẫu dễ sai
   như đếm khách theo `customer_id` thay vì `customer_unique_id`, hoặc suy ra
   đơn đã giao chỉ từ thời điểm giao mà không lọc `order_status`.

SQL hợp lệ về cú pháp/schema vẫn không tự động đúng nghĩa nghiệp vụ. Hãy dùng
ngữ cảnh RAG, benchmark và kiểm tra SQL hiển thị trước khi dựa vào kết quả.

## Chạy giao diện

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

Giao diện có ô chat, toggle RAG, SQL candidate, trạng thái validator, cảnh báo
nghiệp vụ, bảng kết quả và biểu đồ cột khi kết quả có cấu trúc phù hợp. Chỉ tối
đa 1.000 dòng được hiển thị để bảo vệ trình duyệt.

## Ranh giới an toàn

- Database luôn mở `read_only=True` và với DuckDB
  `enable_external_access=false`.
- Chỉ SQL được `validate_sql()` chấp thuận mới đến hàm thực thi.
- Truy vấn bị từ chối không chạy và lỗi được hiển thị rõ trong giao diện.
- Tệp/URL/nguồn ngoài không nằm trong phạm vi truy cập của SQL do LLM tạo.
- Giới hạn hiển thị không thay thế việc tối ưu truy vấn; bộ dữ liệu lớn cần
  thêm policy timeout/chi phí trước khi triển khai đa người dùng.
