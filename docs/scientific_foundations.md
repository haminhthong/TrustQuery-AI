# Cơ sở Khoa học và Nghiên cứu Liên quan (Scientific Foundations)

Dự án **TrustQuery-AI** được xây dựng dựa trên các nghiên cứu học thuật tiên tiến nhất trong các lĩnh vực: **Text-to-SQL**, **Retrieval-Augmented Generation (RAG) cho quy tắc nghiệp vụ cơ sở dữ liệu**, và **An toàn thực thi truy vấn (Safe Execution & Sandboxing)**.

Tài liệu này tổng hợp các bài báo khoa học nền tảng, phân tích đóng góp của từng nghiên cứu và làm rõ cách TrustQuery-AI ứng dụng hoặc giải quyết các thách thức mà các nghiên cứu đã chỉ ra.

---

## 1. Nghiên cứu nền tảng về Text-to-SQL và Thách thức Thực tế

### 1.1. BIRD Benchmark: Thách thức Kiến thức Ngoại vi và Dữ liệu Doanh nghiệp Thực tế
* **Bài báo:** *Can LLM Already Serve as A Database Interface? A BIg Bench for Large-Scale Database Grounded Text-to-SQLs*
* **Tác giả:** Jinyang Li, Binyuan Hui, Ge Qu, Jiaxi Yang, Binhua Li, Bowen Li, Bailin Wang, Bowen Qin, Rongyu Cao, Ruiying Geng, Nan Huo, Xuanhe Zhou, Chenhao Ma, Guoliang Li, Kevin C.C. Chang, Fei Huang, Reynold Cheng, Yongbin Li.
* **Hội nghị / Xuất bản:** **NeurIPS 2023** (Advances in Neural Information Processing Systems).
* **Liên kết:** [arXiv:2305.03111](https://arxiv.org/abs/2305.03111)
* **Ý nghĩa đối với TrustQuery-AI:**
  - BIRD chứng minh rằng các mô hình ngôn ngữ lớn (LLMs) dù rất mạnh nhưng sụt giảm độ chính xác nghiêm trọng khi đối mặt với cơ sở dữ liệu doanh nghiệp thực tế. Hai nguyên nhân lớn nhất là **External Knowledge Grounding** (thiếu hiểu biết về quy ước nghiệp vụ ngầm định) và **Database Value Ambiguity** (sự mập mờ giữa các cột và giá trị).
  - TrustQuery-AI áp dụng trực tiếp bài học này: Trong tập dữ liệu Olist, cột `customer_id` không phải là một khách hàng duy nhất mà là mã phiên giao dịch đơn hàng; khách hàng thực tế là `customer_unique_id`. Nếu không có lớp tri thức ngoại vi (External Knowledge), mô hình sẽ sinh ra SQL sai ngữ nghĩa 100%. TrustQuery-AI giải quyết triệt để thách thức này bằng hệ thống RAG quy tắc nghiệp vụ.

### 1.2. DIN-SQL: Phân rã Bài toán và Tự sửa lỗi (Self-Correction)
* **Bài báo:** *DIN-SQL: Decomposed In-Context Learning of Text-to-SQL with Self-Correction*
* **Tác giả:** Mohammadreza Pourreza, Davood Rafiei.
* **Hội nghị / Xuất bản:** **NeurIPS 2023**.
* **Liên kết:** [arXiv:2304.11015](https://arxiv.org/abs/2304.11015)
* **Ý nghĩa đối với TrustQuery-AI:**
  - DIN-SQL đề xuất chia quá trình sinh Text-to-SQL thành các bước chuyên biệt: liên kết schema (Schema Linking), phân loại độ phức tạp, sinh câu truy vấn con, và tự sửa lỗi (Self-Correction) dựa trên phản hồi của bộ máy kiểm tra.
  - TrustQuery-AI kế thừa tư tưởng này bằng kiến trúc phân tách nghiêm ngặt: LLM chỉ chịu trách nhiệm sinh *SQL đề xuất*; sau đó bộ xác thực (`sql_validator.py`) dùng Abstract Syntax Tree (AST) từ SQLGlot và kiểm tra DuckDB `EXPLAIN` để phát hiện lỗi trước khi cho phép chạy.

### 1.3. Spider Benchmark: Chuẩn mực Đánh giá Text-to-SQL Đa miền
* **Bài báo:** *Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing的一次 Text-to-SQL*
* **Tác giả:** Tao Yu, Rui Zhang, Kai Yang, Michihiro Yasunaga, Dongxu Wang, Zifan Li, James Ma, Irene Li, Qingning Yao, Shanelle Roman, Zilin Zhang, Dragomir Radev.
* **Hội nghị / Xuất bản:** **EMNLP 2018**.
* **Liên kết:** [arXiv:1809.08887](https://arxiv.org/abs/1809.08887)
* **Ý nghĩa đối với TrustQuery-AI:**
  - Định hình phương pháp đánh giá Text-to-SQL bằng Execution Accuracy (đối chiếu kết quả thực thi bảng) thay vì String Match thô sơ, vì nhiều câu lệnh SQL có cú pháp khác nhau nhưng mang lại kết quả ngữ nghĩa tương đương. TrustQuery-AI thiết kế bộ benchmark (`benchmarks/questions.json`) theo nguyên tắc chuẩn mực này.

---

## 2. Nghiên cứu về RAG và Tri thức Nghiệp vụ trong Cơ sở Dữ liệu

### 2.1. Tiêm Tri thức Cơ sở Dữ liệu Chuyên ngành vào LLM
* **Bài báo:** *Enhancing Text-to-SQL Capabilities of Large Language Models via Domain Database Knowledge Injection*
* **Tác giả:** Zhenbiao Ho, et al.
* **Xuất bản:** **arXiv:2409.15907 (2024)**.
* **Liên kết:** [arXiv:2409.15907](https://arxiv.org/abs/2409.15907)
* **Ý nghĩa đối với TrustQuery-AI:**
  - Nghiên cứu chứng minh rằng việc truy xuất đúng các định nghĩa nghiệp vụ và ràng buộc miền (domain constraints) rồi tiêm vào prompt giúp triệt tiêu hiện tượng ảo giác (hallucination) về tên cột và công thức tính.
  - TrustQuery-AI hiện thực hóa điều này bằng ChromaDB cục bộ với mô hình nhúng đa ngôn ngữ `paraphrase-multilingual-MiniLM-L12-v2`, lập chỉ mục các file Markdown quy tắc nghiệp vụ có kiểm soát phiên bản trong Git.

### 2.2. Tối ưu hóa Kích thước Ngữ cảnh trong RAG-Text2SQL
* **Bài báo:** *Balancing Content Size in RAG-Text2SQL System*
* **Xuất bản:** **2024**.
* **Ý nghĩa đối với TrustQuery-AI:**
  - Việc đưa quá nhiều tài liệu không liên quan vào prompt làm tăng độ nhiễu và khiến LLM bỏ sót schema thực tế. Ngược lại, ngữ cảnh quá ngắn lại thiếu định nghĩa.
  - TrustQuery-AI áp dụng kỹ thuật **Heading-level Semantic Chunking** (`chunk_markdown`): Tách nhỏ tài liệu theo tiêu đề cấp 2 (`##`), bảo lưu tiêu đề cấp 1 (`#`) làm ngữ cảnh bao quát, và chỉ truy xuất Top-3 chunks có độ tương đồng cosine cao nhất.

---

## 3. Nghiên cứu về An toàn Truy vấn và Bảo mật trong AI Database Interface

### 3.1. Phòng thủ SQL Injection và Rủi ro Thực thi Mã từ LLM
* **Vấn đề:** Các mô hình tạo sinh có thể bị Prompt Injection hoặc tự sinh ra các câu lệnh nguy hiểm:
  - DDL/DML: `DROP TABLE`, `UPDATE`, `INSERT`, `ALTER`.
  - Các hàm đọc tài nguyên hệ thống hoặc side-channel data exfiltration: `read_csv()`, `http_get()`, `read_parquet()`, `glob()`.
* **Giải pháp đa tầng của TrustQuery-AI:**
  1. **Lớp 1 - Regex & Keyword Filtering:** Chặn ngay từ khóa DDL/DML và các hàm đọc ngoài trước khi phân tích sâu (`src/validation/sql_safety.py`).
  2. **Lớp 2 - AST Static Analysis (SQLGlot):** Phân tích cây cú pháp trừu tượng, xác nhận đúng một câu truy vấn `SELECT` hoặc `WITH ... SELECT`, xác thực danh sách bảng/cột allowlist (`src/validation/sql_validator.py`).
  3. **Lớp 3 - Engine-level Sandboxing:** DuckDB được cấu hình ở cấp độ kết nối C++:
     ```python
     duckdb.connect(db_path, read_only=True, config={"enable_external_access": False})
     ```
     Điều này đảm bảo ngay cả khi một câu lệnh vượt qua validator, bộ máy DuckDB vẫn chặn đứng hoàn toàn mọi nỗ lực truy cập file cục bộ, kết nối mạng hay ghi dữ liệu.

---

## 4. Bảng Đối chiếu Nghiên cứu và Thiết kế Hệ thống

| Thách thức Khoa học | Nghiên cứu Tiêu biểu | Giải pháp trong TrustQuery-AI |
| :--- | :--- | :--- |
| **Mập mờ ngữ nghĩa & Quy tắc ngầm định** | BIRD Benchmark (NeurIPS 2023), Knowledge Injection (2024) | RAG ChromaDB truy xuất định nghĩa khách mua lại, giao trễ, doanh thu |
| **Tách biệt Sinh mã và Kiểm tra** | DIN-SQL (NeurIPS 2023), MAC-SQL (2024) | Quy trình LLM Generator ➔ SQLGlot AST Validator ➔ Read-Only Execution |
| **Độ chính xác Đánh giá** | Spider Benchmark (EMNLP 2018) | Benchmark A/B đối chiếu Execution Output với Reference SQL |
| **Bảo mật và Tránh rò rỉ dữ liệu** | AI Safe Execution & Guardrails | Allowlist AST + DuckDB `enable_external_access=false` Sandbox |
| **Bẫy nhân dòng dữ liệu (Join Fan-out)** | Database Query Optimization & Analytics | Validator chủ động chặn phép `SUM` kết hợp `order_items` và `order_payments` |
