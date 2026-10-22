"""Run a small, read-only smoke check against the local Olist database.

Run from the repository root:
    python -m src.database.smoke_database
"""

from __future__ import annotations

from pathlib import Path

from src.console import configure_utf8_output
from src.database.read_only import connect_read_only


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "database" / "olist.duckdb"


def main() -> None:
    """Print representative metadata and queries without changing the database."""
    configure_utf8_output()
    if not DB_PATH.is_file():
        raise FileNotFoundError(
            "Chưa có database. Hãy chạy python -m src.database.load_data trước."
        )

    connection = connect_read_only(DB_PATH)
    try:
        print("DANH SÁCH BẢNG:")
        print(connection.execute("SHOW TABLES").df().to_string(index=False))

        print("\n10 ĐƠN HÀNG ĐẦU TIÊN:")
        orders = connection.execute(
            """
            SELECT
                order_id,
                customer_id,
                order_status,
                order_purchase_timestamp
            FROM orders
            ORDER BY order_purchase_timestamp, order_id
            LIMIT 10
            """
        ).df()
        print(orders.to_string(index=False))

        print("\nSỐ ĐƠN THEO TRẠNG THÁI:")
        status_counts = connection.execute(
            """
            SELECT
                order_status,
                COUNT(*) AS total_orders
            FROM orders
            GROUP BY order_status
            ORDER BY total_orders DESC, order_status
            """
        ).df()
        print(status_counts.to_string(index=False))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
