"""Tests for presentation and input validation in the RAG retriever."""

import pytest

from src.rag.retriever import (
    IndexModelMismatchError,
    RetrievedChunk,
    format_context,
    get_rule_collection,
    retrieve_context,
)


def test_format_context_preserves_retrieval_order_and_source_information() -> None:
    chunks = [
        RetrievedChunk(
            text="Khách mua lại dùng customer_unique_id.",
            source="rag_business_definitions.md",
            section="Khách hàng mua lại",
            distance=0.12,
        ),
        RetrievedChunk(
            text="Một customer_id dùng để nối đơn hàng.",
            source="olist_schema.md",
            section="customers",
            distance=0.30,
        ),
    ]

    context = format_context(chunks)

    assert "Khách mua lại dùng customer_unique_id." in context
    assert "rag_business_definitions.md" in context
    assert "Khách hàng mua lại" in context
    assert context.index("customer_unique_id") < context.index("Một customer_id")


def test_format_context_returns_empty_string_when_nothing_matches() -> None:
    assert format_context([]) == ""


def test_retrieve_context_rejects_a_blank_question_before_loading_models() -> None:
    with pytest.raises(ValueError, match="Câu hỏi"):
        retrieve_context("   ")


def test_get_rule_collection_rejects_a_different_embedding_model() -> None:
    class Collection:
        metadata = {"embedding_model": "indexed-model"}

    class Client:
        def list_collections(self) -> list[str]:
            return ["rules"]

        def get_collection(self, *, name: str) -> Collection:
            assert name == "rules"
            return Collection()

    with pytest.raises(IndexModelMismatchError, match="indexed-model"):
        get_rule_collection(Client(), "rules", model_name="query-model")
