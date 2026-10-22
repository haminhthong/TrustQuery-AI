# Định nghĩa nghiệp vụ dùng cho RAG

Tài liệu này là nguồn ngữ cảnh nghiệp vụ cho hệ thống Text-to-SQL. Mục tiêu là
giúp mô hình hiểu đúng ý nghĩa của chỉ số, **không** suy đoán chỉ từ tên cột.
Các quy tắc dưới đây bổ sung cho `docs/business_rules/initial_rules.md` và phải
được ưu tiên khi câu hỏi dùng các cụm từ đồng nghĩa như “tổng”, “đã giao”,
“khách quay lại”, “sản phẩm bán”, “trễ” hoặc “đánh giá”.

## Quy ước chung về phạm vi và grain

- Luôn xác định bảng có grain phù hợp trước khi đếm hoặc cộng. `orders` có một
  dòng kỳ vọng cho mỗi `order_id`; `order_items` có một dòng hàng; và
  `order_payments` có một bản ghi thanh toán.
- Không làm thay đổi ý nghĩa của chỉ số bằng một `JOIN` một-nhiều. Nếu chỉ số
  ở cấp đơn hàng nhưng cần nối sang `order_items`, dùng `COUNT(DISTINCT
  o.order_id)` hoặc tổng hợp bảng con về một dòng mỗi đơn trước.
- Nếu người hỏi không nêu phạm vi trạng thái (ví dụ chỉ nói “đơn hàng” hoặc
  “khách đã mua”), không tự ý chỉ lấy đơn `delivered`. Chỉ lọc trạng thái khi
  câu hỏi hoặc định nghĩa chỉ số yêu cầu rõ.
- Cột thời gian giao và các cột nullable không được dùng để tự suy luận trạng
  thái đơn. Trạng thái đơn hàng được xác định bằng `orders.order_status`.

## Tổng đơn hàng

**Ý nghĩa:** số đơn hàng khác nhau trong `orders`.

- Khóa định danh của đơn là `orders.order_id`.
- Công thức an toàn theo định nghĩa: `COUNT(DISTINCT order_id)` từ `orders`.
  Khi việc kiểm tra dữ liệu đã xác nhận `order_id` là duy nhất trong bảng
  `orders`, `COUNT(*)` cho cùng kết quả và là cách dùng trong benchmark Q001.
- Không nối `orders` với `order_items`, `order_payments` hoặc `order_reviews`
  trước khi đếm tổng đơn, vì mỗi đơn có thể tạo ra nhiều dòng sau JOIN.
- “Tổng số đơn”, “bao nhiêu đơn hàng”, “số đơn” và “orders” cùng chỉ số này,
  trừ khi câu hỏi kèm điều kiện khác như trạng thái, năm hoặc thành phố.

Ví dụ SQL ở cấp đơn hàng:

```sql
SELECT COUNT(DISTINCT order_id) AS total_orders
FROM orders;
```

## Đơn đã giao (delivered orders)

**Ý nghĩa:** đơn có trạng thái chính xác là `order_status = 'delivered'` trong
bảng `orders`.

- Không suy ra “đã giao” từ `order_delivered_customer_date` có giá trị.
- Nếu cần số lượng đơn đã giao, đếm `order_id` không trùng sau khi lọc trạng
  thái. Không đếm dòng của `order_items` hay `order_payments`.
- Các cách diễn đạt “đơn giao thành công”, “đơn completed” trong ngữ cảnh bộ
  dữ liệu này cần được ánh xạ về điều kiện `order_status = 'delivered'`, không
  phải một trạng thái tự suy đoán khác.

```sql
SELECT COUNT(DISTINCT order_id) AS delivered_orders
FROM orders
WHERE order_status = 'delivered';
```

## Khách hàng duy nhất (unique customers)

**Ý nghĩa:** một người mua thực tế được nhận diện bằng
`customers.customer_unique_id`, không phải `customers.customer_id`.

- `customer_id` là mã khách hàng gắn với một lần/đơn hàng và một người thực tế
  có thể có nhiều `customer_id`.
- Khi đếm khách hàng đã mua trong một phạm vi đơn hàng, nối
  `orders.customer_id = customers.customer_id`, rồi dùng
  `COUNT(DISTINCT c.customer_unique_id)`.
- Không dùng `COUNT(DISTINCT o.customer_id)` để trả lời “bao nhiêu khách hàng
  duy nhất”, vì nó có thể đếm một người nhiều lần.
- Nếu câu hỏi chỉ nói “khách hàng trong bảng customers” mà không nói đã mua,
  cần làm rõ hoặc đếm distinct `customer_unique_id` trực tiếp trong
  `customers`; đây là tập thực thể, không nhất thiết là tập đơn trong một kỳ.

```sql
SELECT COUNT(DISTINCT c.customer_unique_id) AS unique_customers
FROM orders AS o
JOIN customers AS c ON c.customer_id = o.customer_id;
```

## Khách hàng mua lại / khách quay lại (repeat purchasers)

**Ý nghĩa:** một `customer_unique_id` có hơn một đơn hàng khác nhau trong
phạm vi được hỏi.

- Nhận diện khách qua `customer_unique_id`; tuyệt đối không nhóm chỉ theo
  `customer_id`.
- Đếm số đơn của mỗi khách bằng `COUNT(DISTINCT o.order_id)`. Một khách mua
  lại khi số này lớn hơn 1.
- “Có bao nhiêu khách hàng đã mua lại?” là số lượng khách thỏa điều kiện, còn
  “tỷ lệ mua lại” cần mẫu số được nêu rõ (thông thường số khách duy nhất trong
  cùng phạm vi) và không được tự bổ sung nếu chưa được hỏi.
- Bộ lọc thời gian, trạng thái hoặc danh mục phải được áp dụng cho đơn/dòng
  hàng trước khi nhóm khách; việc dùng lọc đó thay đổi định nghĩa mua lại.

```sql
SELECT COUNT(*) AS repeat_customers
FROM (
  SELECT c.customer_unique_id
  FROM orders AS o
  JOIN customers AS c ON c.customer_id = o.customer_id
  GROUP BY c.customer_unique_id
  HAVING COUNT(DISTINCT o.order_id) > 1
) AS repeat_customer_ids;
```

## Số sản phẩm bán / số dòng hàng đã bán (items sold)

**Ý nghĩa mặc định:** số dòng trong `order_items`, tức số lượng dòng hàng bán
ra theo định nghĩa đã chọn.

- Mỗi dòng hàng được định danh kỳ vọng bởi cặp (`order_id`, `order_item_id`).
  Đếm `COUNT(*)` từ `order_items` khi không có JOIN làm nhân dòng.
- Chỉ số này **không** phải số sản phẩm khác nhau (`COUNT(DISTINCT
  product_id)`) và cũng không phải số đơn (`COUNT(DISTINCT order_id)`).
- Khi cần chia theo danh mục, nối `order_items` với `products` qua
  `product_id`, rồi đếm dòng `order_items`. Đây là định nghĩa của benchmark
  Q004.
- Nếu “số lượng đơn vị” được hiểu là số đơn vị vật lý và dữ liệu có một cột
  số lượng, cần dùng cột đó; lược đồ hiện tại không có cột quantity riêng nên
  không được tự giả định số lượng lớn hơn một cho một dòng hàng.

```sql
SELECT COUNT(*) AS items_sold
FROM order_items;
```

## Giá trị thanh toán (total payment value)

**Ý nghĩa:** tổng của `order_payments.payment_value` trong phạm vi được nêu.

- Một đơn có thể có nhiều bản ghi trong `order_payments` (ví dụ nhiều kỳ hoặc
  nhiều phương thức); phải cộng **mọi** `payment_value` phù hợp.
- Khi lọc theo thuộc tính đơn (năm đặt hàng, trạng thái giao, khách hàng), nối
  `orders` với `order_payments` qua `order_id`, rồi `SUM(payment_value)`.
- Không JOIN đồng thời `order_items` và `order_payments` ở dữ liệu chưa tổng
  hợp: một đơn có nhiều dòng ở cả hai bảng sẽ làm nhân `payment_value`.
- Gọi kết quả là `total_payment` hoặc “giá trị thanh toán”. Không tự gọi là
  “doanh thu” nếu chưa có định nghĩa phê duyệt về phí vận chuyển, đơn hủy,
  hoàn tiền và điều chỉnh.

```sql
SELECT SUM(op.payment_value) AS total_payment
FROM orders AS o
JOIN order_payments AS op ON op.order_id = o.order_id
WHERE o.order_status = 'delivered';
```

## Giao hàng trễ (late delivery)

**Ý nghĩa:** một đơn giao trễ khi thời gian giao thực tế cho khách hàng muộn
hơn thời gian giao dự kiến:

`order_delivered_customer_date > order_estimated_delivery_date`.

- Dùng các cột của bảng `orders`: thời điểm giao thực tế là
  `order_delivered_customer_date`; thời điểm dự kiến là
  `order_estimated_delivery_date`.
- Với số đơn giao trễ, chỉ xét các đơn có cả hai mốc thời gian khác `NULL` và
  thông thường lọc `order_status = 'delivered'`. Điều kiện đầy đủ giúp tránh
  diễn giải đơn chưa hoàn tất là đúng hạn hoặc trễ.
- “Trễ bao nhiêu ngày” là chênh lệch giữa hai mốc trên; chọn hàm ngày/giờ của
  DuckDB phù hợp với đơn vị mà câu hỏi yêu cầu. Không dùng ngày đặt hàng hoặc
  ngày duyệt thay cho thời điểm giao thực tế.
- Nếu tính tỷ lệ giao trễ, mẫu số nên là các đơn delivered có đủ hai mốc thời
  gian, trừ khi yêu cầu nêu một mẫu số khác.

```sql
SELECT COUNT(DISTINCT order_id) AS late_deliveries
FROM orders
WHERE order_status = 'delivered'
  AND order_delivered_customer_date IS NOT NULL
  AND order_estimated_delivery_date IS NOT NULL
  AND order_delivered_customer_date > order_estimated_delivery_date;
```

## Điểm đánh giá (review score)

**Ý nghĩa:** giá trị `order_reviews.review_score` của một bản ghi đánh giá.

- Khi hỏi điểm đánh giá trung bình, dùng `AVG(review_score)`; khi hỏi phân bố,
  nhóm theo `review_score` và đếm bản ghi review.
- `order_reviews` có grain là một bản ghi đánh giá gắn với đơn. Không giả định
  `order_id` hay `review_id` là duy nhất nếu chưa kiểm tra dữ liệu.
- Khi kết hợp với trạng thái hoặc thời gian của đơn, nối với `orders` theo
  `order_id`. Tránh JOIN thêm các bảng một-nhiều như `order_items` trước khi
  tính trung bình vì sẽ tăng trọng số của các đơn nhiều dòng hàng.
- “Điểm hài lòng” hoặc “rating” cần được ánh xạ về `review_score` trong phạm
  vi dữ liệu này; không suy diễn sentiment từ nội dung nhận xét.

```sql
SELECT AVG(review_score) AS average_review_score
FROM order_reviews;
```

## Cảnh báo JOIN quan trọng cho mọi truy vấn

`orders` → `order_items` và `orders` → `order_payments` đều là quan hệ một
đến nhiều. Không nối cả hai rồi thực hiện `SUM(price)`, `SUM(freight_value)`
hoặc `SUM(payment_value)` trừ khi một hoặc cả hai phía đã được tổng hợp về một
dòng cho mỗi `order_id`. Nếu cần cả chỉ số hàng và thanh toán trong cùng một
kết quả, hãy tạo các CTE tổng hợp riêng ở cấp `order_id` rồi mới JOIN chúng.
