"""Load the nine Olist CSV files into a local DuckDB database.

Run from the repository root:
    python -m src.database.load_data
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from src.console import configure_utf8_output

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "database" / "olist.duckdb"

TABLES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}


def quote_identifier(identifier: str) -> str:
    """Quote a SQL identifier while preserving a fixed, readable table name."""
    return '"' + identifier.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Quote a DuckDB string literal (used here only for local file paths)."""
    return "'" + value.replace("'", "''") + "'"


def validate_source_files(raw_dir: Path = RAW_DIR) -> None:
    """Fail before opening/modifying the database when a source CSV is missing."""
    missing = [filename for filename in TABLES.values() if not (raw_dir / filename).is_file()]
    if missing:
        formatted = "\n  - ".join(missing)
        raise FileNotFoundError(
            "Thiếu các CSV Olist bắt buộc:\n"
            f"  - {formatted}\n"
            f"Hãy giải nén chúng vào: {raw_dir}"
        )


def load_data(raw_dir: Path = RAW_DIR, db_path: Path = DB_PATH) -> None:
    """Replace the nine target tables atomically from the raw Olist CSV files."""
    validate_source_files(raw_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("BEGIN TRANSACTION")
        for table, filename in TABLES.items():
            csv_path = (raw_dir / filename).resolve().as_posix()
            connection.execute(
                f"""
                CREATE OR REPLACE TABLE {quote_identifier(table)} AS
                SELECT *
                FROM read_csv_auto({quote_literal(csv_path)})
                """
            )
            count = connection.execute(
                f"SELECT COUNT(*) FROM {quote_identifier(table)}"
            ).fetchone()[0]
            print(f"[OK] {table}: {count:,} dòng")
        connection.execute("COMMIT")
        print("\nĐã tạo database tại:")
        print(db_path)
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def main() -> None:
    configure_utf8_output()
    load_data()


if __name__ == "__main__":
    main()
