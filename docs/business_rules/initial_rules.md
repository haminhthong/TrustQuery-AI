# Quy tắc nghiệp vụ ban đầu

Trạng thái: bản khởi đầu để đánh giá câu SQL. Các quy tắc này xuất phát từ phạm vi tuần 1 và phải được cập nhật khi có quyết định nghiệp vụ mới.

## Nguyên tắc dữ liệu và an toàn

- Không sửa, xóa, điền giá trị thiếu hoặc ghi đè tệp trong `data/raw/`.
- Giá trị thiếu không mặc định là lỗi. Ví dụ, đơn chưa giao có thể không có thời điểm giao thực tế.
- Database dùng cho các truy vấn của AI phải được mở ở chế độ chỉ đọc (`read_only=True`).
- SQL do LLM tạo chỉ là đề xuất ở giai đoạn này: phải được người dùng đọc, xác nhận an toàn và chạy thủ công. Không cấp cho LLM quyền tự sửa hoặc xóa dữ liệu.

## Đơn vị phân tích

| Khái niệm | Định nghĩa ban đầu |
| --- | --- |
| Đơn hàng | Một dòng trong `orders`, xác định bởi `order_id`. |
| Dòng hàng đã bán | Một dòng trong `order_items`; không đồng nghĩa với sản phẩm khác nhau hoặc đơn hàng khác nhau. |
| Khách hàng quay lại | Phân tích theo `customers.customer_unique_id`, không phải chỉ theo `customer_id`. |
| Bản ghi thanh toán | Một dòng trong `order_payments`; một đơn có thể có nhiều bản ghi. |
| Giá trị thanh toán | Tổng `payment_value` theo phạm vi được nêu rõ. Không tự gọi là “doanh thu” nếu chưa có định nghĩa nghiệp vụ được phê duyệt. |

## Định nghĩa cho bộ benchmark ban đầu

| Mã | Chỉ số/câu hỏi | Quy tắc chính xác |
| --- | --- | --- |
| Q001 | Tổng số đơn hàng | `COUNT(*)` từ `orders`; không JOIN sang bảng khác. |
| Q002 | Số đơn đã giao | Đếm dòng `orders` có `order_status = 'delivered'`. |
| Q003 | Các năm có đơn hàng | Nhóm theo năm của `order_purchase_timestamp`, không theo ngày duyệt hay ngày giao. |
| Q004 | Top 5 danh mục bán nhiều nhất | Đếm số dòng `order_items` sau khi JOIN với `products`; không dùng `COUNT(DISTINCT product_id)` hay `COUNT(DISTINCT order_id)`. |
| Q005 | Giá trị thanh toán của đơn đã giao theo năm | JOIN `orders` với `order_payments` theo `order_id`, lọc `delivered`, cộng mọi `payment_value`, nhóm theo năm đặt hàng. Nhãn kết quả là `total_payment`. |

## Quy tắc thời gian

- “Theo năm” trong Q003 và Q005 nghĩa là năm của `order_purchase_timestamp`.
- Phạm vi năm 2018 được biểu diễn an toàn bằng khoảng nửa kín: `order_purchase_timestamp >= '2018-01-01' AND order_purchase_timestamp < '2019-01-01'`.
- Không suy luận múi giờ, ngày hoàn tiền hoặc thời điểm ghi nhận doanh thu nếu chưa có quy tắc bổ sung.

## Quy tắc JOIN và tổng hợp

- `orders` → `order_items` có thể nhân một đơn thành nhiều dòng hàng.
- `orders` → `order_payments` có thể nhân một đơn thành nhiều bản ghi thanh toán.
- Không được JOIN đồng thời `order_items` và `order_payments` rồi cộng `price`, `freight_value` hoặc `payment_value` ở dữ liệu chưa tổng hợp. Đây là nguy cơ nhân bản kết quả.
- Khi cần dùng cả hai bảng, tổng hợp từng bảng về đúng grain (thường là một dòng mỗi `order_id`) trước rồi mới JOIN.
- Với bảng `geolocation`, phải chuẩn hóa một dòng cho mỗi tiền tố mã bưu chính trước khi dùng để tổng hợp; tiền tố có thể xuất hiện nhiều lần.
- Không loại bỏ danh mục hoặc sản phẩm có dữ liệu thiếu trừ khi câu hỏi yêu cầu rõ điều đó. Khi cần bản dịch danh mục, ưu tiên `LEFT JOIN` để giữ lại các bản ghi không có bản dịch.

## Trạng thái đơn và giá trị thiếu

- Chỉ gọi “đơn đã giao” khi `order_status = 'delivered'`.
- Không suy ra trạng thái từ việc một cột thời gian có hoặc không có giá trị.
- Các trường thời gian giao hàng nullable phải được diễn giải theo ngữ cảnh quy trình, không tự điền dữ liệu.

## Điểm còn chờ quyết định nghiệp vụ

Những nội dung sau chưa được định nghĩa trong phạm vi hiện tại và không được LLM tự suy đoán:

1. “Doanh thu” có bao gồm phí vận chuyển, đơn hủy, hoàn tiền hoặc điều chỉnh thanh toán hay không.
2. Cách tính doanh thu/giá trị đơn khi có nhiều phương thức hoặc nhiều kỳ thanh toán.
3. Cách xử lý danh mục `NULL`, bản dịch thiếu và vị trí địa lý trùng lặp.
4. Múi giờ và sự kiện thời gian chuẩn cho các báo cáo vận hành ngoài các benchmark hiện tại.
