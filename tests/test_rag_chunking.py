"""Dependency-free tests for splitting business-rule Markdown into RAG chunks."""

from src.rag.build_index import chunk_markdown


def test_chunk_markdown_keeps_each_business_definition_with_its_heading() -> None:
    markdown = """# Quy tắc nghiệp vụ

Phần giới thiệu chung.

## Khách hàng mua lại

Nhận diện bằng `customer_unique_id`, không phải `customer_id`.

## Giao hàng trễ

So sánh `order_delivered_customer_date` với `order_estimated_delivery_date`.
"""

    chunks = chunk_markdown(markdown, source="business_rules/example.md")

    assert [chunk.section for chunk in chunks] == [
        "Khách hàng mua lại",
        "Giao hàng trễ",
    ]
    assert [chunk.source for chunk in chunks] == [
        "business_rules/example.md",
        "business_rules/example.md",
    ]
    assert "customer_unique_id" in chunks[0].text
    assert "order_estimated_delivery_date" in chunks[1].text
