"""Print a non-destructive profile for every required Olist CSV file.

Run from the repository root:
    python src/database/inspect_data.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "raw"

EXPECTED_FILES = (
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
)


def find_csv_files(data_dir: Path = DATA_DIR) -> list[Path]:
    """Return the required CSV files, with a useful error if extraction is incomplete."""
    if not data_dir.exists():
        raise FileNotFoundError(
            f"Không tìm thấy thư mục dữ liệu: {data_dir}. "
            "Hãy giải nén bộ dữ liệu Olist vào data/raw/."
        )

    missing = [name for name in EXPECTED_FILES if not (data_dir / name).is_file()]
    if missing:
        formatted = "\n  - ".join(missing)
        raise FileNotFoundError(
            "Thiếu các CSV Olist bắt buộc:\n"
            f"  - {formatted}\n"
            f"Thư mục mong đợi: {data_dir}"
        )

    return [data_dir / name for name in EXPECTED_FILES]


def inspect_file(file: Path) -> None:
    """Print the requested descriptive statistics without changing source data."""
    dataframe = pd.read_csv(file, low_memory=False)

    print("=" * 60)
    print("FILE:", file.name)
    print("Số dòng:", len(dataframe))
    print("Số cột:", len(dataframe.columns))
    print("Số dòng trùng hoàn toàn:", dataframe.duplicated().sum())
    print("\nCÁC CỘT:")
    print(dataframe.dtypes.to_string())
    print("\nGIÁ TRỊ THIẾU:")
    print(dataframe.isna().sum().to_string())
    print("\n5 DÒNG ĐẦU:")
    print(dataframe.head(5).to_string(index=False))
    print()


def main() -> None:
    csv_files = find_csv_files()
    print(f"Tìm thấy {len(csv_files)} file CSV\n")
    for file in csv_files:
        inspect_file(file)


if __name__ == "__main__":
    main()
