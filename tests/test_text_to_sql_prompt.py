"""Tests for the schema-only and RAG-enhanced Text-to-SQL prompts."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.llm import text_to_sql


build_prompt = text_to_sql.build_prompt


def test_build_prompt_without_context_keeps_schema_only_variant() -> None:
    """An empty context must leave a usable baseline (non-RAG) prompt."""
    prompt = build_prompt(
        question="Có bao nhiêu đơn đã giao?",
        schema="orders: order_id (VARCHAR), order_status (VARCHAR)",
    )

    assert "orders: order_id" in prompt
    assert "Có bao nhiêu đơn đã giao?" in prompt
    assert "Business context:" not in prompt


def test_build_prompt_adds_retrieved_business_context_in_its_own_section() -> None:
    """Retrieved rules are visible to the model without replacing the schema."""
    context = (
        "Khách hàng mua lại được nhận diện bằng customer_unique_id, "
        "không phải customer_id."
    )

    prompt = build_prompt(
        question="Có bao nhiêu khách hàng đã mua lại?",
        schema="customers: customer_id (VARCHAR), customer_unique_id (VARCHAR)",
        context=context,
    )

    assert "customers: customer_id" in prompt
    assert context in prompt
    assert "Business context:" in prompt
    assert prompt.index("Business context:") < prompt.index("Question:")


def test_extract_sql_accepts_one_fenced_block_with_surrounding_prose() -> None:
    response = "Here is the query:\n```sql\nSELECT 1 AS result\n```\n"

    assert text_to_sql.extract_sql(response) == "SELECT 1 AS result"


def test_extract_sql_rejects_ambiguous_multiple_fenced_blocks() -> None:
    response = "```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```"

    with pytest.raises(RuntimeError, match="nhiều khối SQL"):
        text_to_sql.extract_sql(response)


def test_generate_sql_uses_resolved_environment_configuration(monkeypatch) -> None:
    """CLI calls must pass `.env` values to the Gemini SDK, not ``None``."""
    captured: dict[str, str] = {}

    class FakeModels:
        def generate_content(self, *, model: str, contents: str) -> SimpleNamespace:
            captured["model"] = model
            captured["contents"] = contents
            return SimpleNamespace(text="SELECT 1 AS result")

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            captured["api_key"] = api_key
            self.models = FakeModels()

    fake_google = ModuleType("google")
    fake_google.genai = SimpleNamespace(Client=FakeClient)
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    def load_test_environment(_path: object) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "key-from-env")
        monkeypatch.setenv("GEMINI_MODEL", "model-from-env")

    monkeypatch.setattr(text_to_sql, "load_dotenv", load_test_environment)

    result = text_to_sql.generate_sql(
        "How many orders are there?", "orders: order_id (VARCHAR)"
    )

    assert result == "SELECT 1 AS result"
    assert captured["api_key"] == "key-from-env"
    assert captured["model"] == "model-from-env"


def test_generate_sql_hides_sdk_error_details(monkeypatch) -> None:
    """Provider failures must not expose a supplied credential in UI/CLI errors."""

    class FailingModels:
        def generate_content(self, **_kwargs: object) -> None:
            raise RuntimeError("provider rejected secret-token-value")

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            self.models = FailingModels()

    fake_google = ModuleType("google")
    fake_google.genai = SimpleNamespace(Client=FakeClient)
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setenv("GEMINI_API_KEY", "key-from-env")
    monkeypatch.setenv("GEMINI_MODEL", "model-from-env")
    monkeypatch.setattr(text_to_sql, "load_dotenv", lambda _path: None)

    with pytest.raises(RuntimeError) as error:
        text_to_sql.generate_sql("Question", "orders: order_id (VARCHAR)")

    assert "Không thể gọi Gemini API" in str(error.value)
    assert "secret-token-value" not in str(error.value)
