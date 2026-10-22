"""Tests for the schema-only and RAG-enhanced Text-to-SQL prompts."""

from src.llm.text_to_sql import build_prompt


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
