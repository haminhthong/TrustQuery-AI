"""Small local Chroma integration test with a deterministic fake embedder."""

from __future__ import annotations

from pathlib import Path

from src.rag import build_index, retriever


class FakeEmbeddingModel:
    """Embed repeat-customer vocabulary close to the first test rule."""

    def encode(self, texts: list[str], **_: object) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            normalized = text.lower()
            vectors.append(
                [
                    float("khách" in normalized or "customer" in normalized),
                    float("giao" in normalized or "delivery" in normalized),
                ]
            )
        return vectors


def test_build_then_retrieve_from_persistent_chroma(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Indexing uses explicit vectors and retrieval retains source metadata."""
    documents_dir = tmp_path / "rules"
    documents_dir.mkdir()
    (documents_dir / "rules.md").write_text(
        """# Quy tắc

## Khách hàng mua lại

Dùng customer_unique_id để nhận diện khách mua lại.

## Giao hàng trễ

So sánh thời gian giao thực tế và thời gian dự kiến.
""",
        encoding="utf-8",
    )
    persist_dir = tmp_path / "chroma"
    fake_model = FakeEmbeddingModel()
    monkeypatch.setattr(build_index, "load_embedding_model", lambda *_args, **_kwargs: fake_model)
    monkeypatch.setattr(retriever, "load_embedding_model", lambda *_args, **_kwargs: fake_model)

    count = build_index.build_index(
        documents_dir=documents_dir,
        persist_dir=persist_dir,
        collection_name="test_business_rules",
        model_name="fake-model",
    )
    matches = retriever.retrieve(
        "Có bao nhiêu khách hàng mua lại?",
        persist_dir=persist_dir,
        collection_name="test_business_rules",
        model_name="fake-model",
        n_results=1,
    )

    assert count == 2
    assert len(matches) == 1
    assert matches[0].source == "rules.md"
    assert matches[0].section == "Khách hàng mua lại"
    assert "customer_unique_id" in matches[0].text
