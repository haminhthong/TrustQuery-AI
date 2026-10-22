# Lược đồ dữ liệu Olist

Tài liệu này mô tả lược đồ ban đầu của chín tệp CSV Olist sau khi được nạp vào DuckDB. Kiểu dữ liệu dưới đây là kiểu **nghiệp vụ kỳ vọng**; kiểu thực tế phải được xác nhận bằng `inspect_data.py` và `DESCRIBE <table>` sau khi nạp dữ liệu.

## Quy ước

- `order_id`, `customer_id`, `product_id`, và `seller_id` là các mã định danh dạng chuỗi.
- Các cột thời gian cần được DuckDB nhận diện là `TIMESTAMP` hoặc được chuyển đổi rõ ràng trước khi dùng hàm thời gian.
- `data/raw/` là dữ liệu nguồn, chỉ đọc và không được sửa trực tiếp.
- “Grain” là mức chi tiết của một dòng; mọi phép tổng hợp phải bắt đầu từ grain phù hợp.

## Các bảng

### `customers`

Grain: một mã `customer_id` dùng trong đơn hàng.

| Cột | Kiểu nghiệp vụ | Diễn giải |
| --- | --- | --- |
| `customer_id` | TEXT | Mã khách hàng dùng để liên kết với `orders.customer_id`; khóa ứng viên. |
| `customer_unique_id` | TEXT | Mã định danh khách hàng thực tế, có thể lặp lại qua nhiều `customer_id`. |
| `customer_zip_code_prefix` | INTEGER/TEXT | Tiền tố mã bưu chính của khách hàng. |
| `customer_city` | TEXT | Thành phố của khách hàng. |
| `customer_state` | TEXT | Bang của khách hàng. |

### `orders`

Grain: một đơn hàng (`order_id`).

| Cột | Kiểu nghiệp vụ | Diễn giải |
| --- | --- | --- |
| `order_id` | TEXT | Mã đơn hàng; khóa ứng viên. |
| `customer_id` | TEXT | Khóa ngoại logic tới `customers.customer_id`. |
| `order_status` | TEXT | Trạng thái đơn hàng. |
| `order_purchase_timestamp` | TIMESTAMP | Thời điểm đặt hàng. |
| `order_approved_at` | TIMESTAMP, nullable | Thời điểm đơn được duyệt. |
| `order_delivered_carrier_date` | TIMESTAMP, nullable | Thời điểm giao cho đơn vị vận chuyển. |
| `order_delivered_customer_date` | TIMESTAMP, nullable | Thời điểm giao cho khách hàng. |
| `order_estimated_delivery_date` | TIMESTAMP, nullable | Ngày giao dự kiến. |

### `order_items`

Grain: một dòng hàng trong đơn hàng (`order_id`, `order_item_id`). Một đơn có thể có nhiều dòng hàng.

| Cột | Kiểu nghiệp vụ | Diễn giải |
| --- | --- | --- |
| `order_id` | TEXT | Khóa ngoại logic tới `orders.order_id`. |
| `order_item_id` | INTEGER | Số thứ tự dòng hàng trong đơn; cùng `order_id` tạo khóa ứng viên. |
| `product_id` | TEXT | Khóa ngoại logic tới `products.product_id`. |
| `seller_id` | TEXT | Khóa ngoại logic tới `sellers.seller_id`. |
| `shipping_limit_date` | TIMESTAMP | Hạn giao hàng của dòng hàng. |
| `price` | DECIMAL | Giá sản phẩm của dòng hàng. |
| `freight_value` | DECIMAL | Phí vận chuyển của dòng hàng. |

### `order_payments`

Grain: một bản ghi thanh toán của đơn hàng (`order_id`, `payment_sequential`). Một đơn có thể có nhiều bản ghi thanh toán.

| Cột | Kiểu nghiệp vụ | Diễn giải |
| --- | --- | --- |
| `order_id` | TEXT | Khóa ngoại logic tới `orders.order_id`. |
| `payment_sequential` | INTEGER | Thứ tự bản ghi thanh toán trong đơn. |
| `payment_type` | TEXT | Loại thanh toán. |
| `payment_installments` | INTEGER | Số kỳ thanh toán. |
| `payment_value` | DECIMAL | Giá trị của bản ghi thanh toán. |

### `order_reviews`

Grain: một bản ghi đánh giá gắn với đơn hàng. Không được giả định `order_id` hoặc `review_id` là duy nhất trước khi kiểm tra dữ liệu.

| Cột | Kiểu nghiệp vụ | Diễn giải |
| --- | --- | --- |
| `review_id` | TEXT | Mã đánh giá. |
| `order_id` | TEXT | Khóa ngoại logic tới `orders.order_id`. |
| `review_score` | INTEGER | Điểm đánh giá. |
| `review_comment_title` | TEXT, nullable | Tiêu đề nhận xét. |
| `review_comment_message` | TEXT, nullable | Nội dung nhận xét. |
| `review_creation_date` | TIMESTAMP | Thời điểm tạo đánh giá. |
| `review_answer_timestamp` | TIMESTAMP | Thời điểm phản hồi đánh giá. |

### `products`

Grain: một sản phẩm (`product_id`).

| Cột | Kiểu nghiệp vụ | Diễn giải |
| --- | --- | --- |
| `product_id` | TEXT | Mã sản phẩm; khóa ứng viên. |
| `product_category_name` | TEXT, nullable | Danh mục bằng tiếng Bồ Đào Nha. |
| `product_name_lenght` | INTEGER, nullable | Độ dài tên sản phẩm; tên cột nguồn có lỗi chính tả `lenght`. |
| `product_description_lenght` | INTEGER, nullable | Độ dài mô tả; tên cột nguồn có lỗi chính tả `lenght`. |
| `product_photos_qty` | INTEGER, nullable | Số ảnh sản phẩm. |
| `product_weight_g` | DECIMAL, nullable | Khối lượng (gram). |
| `product_length_cm` | DECIMAL, nullable | Chiều dài (cm). |
| `product_height_cm` | DECIMAL, nullable | Chiều cao (cm). |
| `product_width_cm` | DECIMAL, nullable | Chiều rộng (cm). |

### `sellers`

Grain: một người bán (`seller_id`).

| Cột | Kiểu nghiệp vụ | Diễn giải |
| --- | --- | --- |
| `seller_id` | TEXT | Mã người bán; khóa ứng viên. |
| `seller_zip_code_prefix` | INTEGER/TEXT | Tiền tố mã bưu chính người bán. |
| `seller_city` | TEXT | Thành phố người bán. |
| `seller_state` | TEXT | Bang người bán. |

### `geolocation`

Grain: một quan sát tọa độ cho tiền tố mã bưu chính; một tiền tố có thể xuất hiện nhiều lần.

| Cột | Kiểu nghiệp vụ | Diễn giải |
| --- | --- | --- |
| `geolocation_zip_code_prefix` | INTEGER/TEXT | Tiền tố mã bưu chính. |
| `geolocation_lat` | DECIMAL | Vĩ độ. |
| `geolocation_lng` | DECIMAL | Kinh độ. |
| `geolocation_city` | TEXT | Thành phố. |
| `geolocation_state` | TEXT | Bang. |

Không nối trực tiếp bảng này với `customers` hoặc `sellers` rồi tổng hợp số tiền/số đơn mà chưa chuẩn hóa một dòng cho mỗi tiền tố; điều đó có thể làm nhân bản kết quả.

### `category_translation`

Grain: một ánh xạ tên danh mục tiếng Bồ Đào Nha sang tiếng Anh.

| Cột | Kiểu nghiệp vụ | Diễn giải |
| --- | --- | --- |
| `product_category_name` | TEXT | Tên danh mục nguồn, dùng để nối với `products.product_category_name`. |
| `product_category_name_english` | TEXT | Bản dịch tiếng Anh. |

## Quan hệ logic và rủi ro JOIN

| Từ | Đến | Lực lượng kỳ vọng | Lưu ý |
| --- | --- | --- | --- |
| `customers.customer_id` | `orders.customer_id` | 1 : 0..N | Dùng `customer_unique_id` khi phân tích khách quay lại, không dùng `customer_id` thay thế. |
| `orders.order_id` | `order_items.order_id` | 1 : 0..N | JOIN làm hạt dữ liệu chuyển từ đơn sang dòng hàng. |
| `orders.order_id` | `order_payments.order_id` | 1 : 0..N | Tổng thanh toán theo đơn cần cộng mọi bản ghi thanh toán của đơn. |
| `orders.order_id` | `order_reviews.order_id` | 1 : 0..N | Kiểm tra số bản ghi review trước khi giả định quan hệ 1:1. |
| `order_items.product_id` | `products.product_id` | N : 1 | An toàn cho đếm dòng hàng nếu `products.product_id` là duy nhất. |
| `order_items.seller_id` | `sellers.seller_id` | N : 1 | Kiểm tra uniqueness của `seller_id` trước khi tổng hợp. |
| `products.product_category_name` | `category_translation.product_category_name` | N : 0..1 | Dùng `LEFT JOIN` nếu vẫn muốn giữ sản phẩm không có bản dịch. |

Không JOIN đồng thời `order_items` và `order_payments` ở mức `order_id` rồi `SUM` giá trị, trừ khi một phía đã được tổng hợp về một dòng trên mỗi `order_id`. Nếu không, một đơn có *m* dòng hàng và *n* thanh toán sẽ tạo *m × n* dòng.

## Kiểm tra cần thực hiện sau khi nạp dữ liệu

1. Đếm dòng, giá trị thiếu và dòng trùng trong từng bảng.
2. Kiểm tra tính duy nhất của các khóa ứng viên nêu trên.
3. Kiểm tra bản ghi mồ côi trước khi coi các quan hệ là khóa ngoại thực tế.
4. Xác nhận kiểu cột thời gian, số tiền và mã bưu chính trong DuckDB.
5. Ghi nhận rõ các cột nullable do quy trình nghiệp vụ, nhất là các mốc giao hàng của đơn chưa hoàn tất.
