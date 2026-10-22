"""Shared pytest behavior for tests that need ignored local Olist artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OLIST_DATABASE_PATH = ROOT / "data" / "database" / "olist.duckdb"


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip integration tests when a fresh clone has no local Olist database."""
    if (
        item.get_closest_marker("requires_olist_database")
        and not OLIST_DATABASE_PATH.is_file()
    ):
        pytest.skip(
            "Cần data/database/olist.duckdb; chạy python -m src.database.load_data trước."
        )
