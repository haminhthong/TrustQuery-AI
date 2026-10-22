"""Retrieve business-rule context from the local ChromaDB collection.

Run after building the index:

    python -m src.rag.retriever "Có bao nhiêu khách hàng đã mua lại?"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.console import configure_utf8_output
from src.rag.build_index import (
    CHROMA_DIR,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    embed_texts,
    get_chroma_client,
    load_embedding_model,
)


class IndexNotBuiltError(RuntimeError):
    """Raised when retrieval is requested before the local index exists."""


class IndexModelMismatchError(RuntimeError):
    """Raised when query embeddings would not match the indexed model."""


@dataclass(frozen=True)
class RetrievedChunk:
    """A business-rule passage returned by a nearest-neighbour search."""

    source: str
    section: str
    text: str
    distance: float | None = None


def _collection_names(client: Any) -> set[str]:
    """Return collection names for both current and older Chroma response shapes."""
    return {
        collection if isinstance(collection, str) else collection.name
        for collection in client.list_collections()
    }


def get_rule_collection(
    client: Any,
    collection_name: str = COLLECTION_NAME,
    model_name: str = EMBEDDING_MODEL,
) -> Any:
    """Open the indexed collection or raise an actionable local error."""
    if collection_name not in _collection_names(client):
        raise IndexNotBuiltError(
            "Chưa có chỉ mục quy tắc nghiệp vụ. Hãy chạy: "
            "python -m src.rag.build_index"
        )
    collection = client.get_collection(name=collection_name)
    metadata = getattr(collection, "metadata", None) or {}
    indexed_model = metadata.get("embedding_model")
    if indexed_model and indexed_model != model_name:
        raise IndexModelMismatchError(
            "Chỉ mục được tạo bằng model embedding "
            f"'{indexed_model}', nhưng truy xuất đang dùng '{model_name}'. "
            "Hãy dùng cùng --model hoặc lập lại chỉ mục."
        )
    return collection


def _first_query_row(result: dict[str, Any], key: str) -> list[Any]:
    """Read one query's result while tolerating omitted optional result fields."""
    values = result.get(key) or []
    return list(values[0]) if values else []


def retrieve(
    question: str,
    *,
    n_results: int = 3,
    persist_dir: Path = CHROMA_DIR,
    collection_name: str = COLLECTION_NAME,
    model_name: str = EMBEDDING_MODEL,
) -> list[RetrievedChunk]:
    """Find the most relevant local business-rule passages for a question."""
    question = question.strip() if isinstance(question, str) else ""
    if not question:
        raise ValueError("Câu hỏi để truy xuất ngữ cảnh không được để trống.")
    if n_results < 1:
        raise ValueError("n_results phải lớn hơn hoặc bằng 1.")

    client = get_chroma_client(persist_dir)
    collection = get_rule_collection(client, collection_name, model_name)
    # An index could not exist without this model having been loaded once, so
    # avoid an unnecessary Hugging Face network check for every user question.
    model = load_embedding_model(model_name, local_files_only=True)
    query_embedding = embed_texts(model, [question])[0]
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    documents = _first_query_row(result, "documents")
    metadatas = _first_query_row(result, "metadatas")
    distances = _first_query_row(result, "distances")
    chunks: list[RetrievedChunk] = []
    for index, text in enumerate(documents):
        if not isinstance(text, str):
            continue
        metadata = metadatas[index] if index < len(metadatas) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        distance = distances[index] if index < len(distances) else None
        chunks.append(
            RetrievedChunk(
                source=str(metadata.get("source", "không rõ nguồn")),
                section=str(metadata.get("section", "không rõ mục")),
                text=text,
                distance=float(distance) if distance is not None else None,
            )
        )
    return chunks


def format_context(chunks: Iterable[RetrievedChunk]) -> str:
    """Format retrieved evidence for the LLM while keeping source attribution."""
    passages = list(chunks)
    if not passages:
        return ""
    formatted: list[str] = []
    for number, chunk in enumerate(passages, start=1):
        formatted.append(
            f"[Nguồn {number}: {chunk.source} — {chunk.section}]\n{chunk.text.strip()}"
        )
    return "\n\n".join(formatted)


def retrieve_context(
    question: str,
    *,
    n_results: int = 3,
    persist_dir: Path = CHROMA_DIR,
    collection_name: str = COLLECTION_NAME,
    model_name: str = EMBEDDING_MODEL,
) -> str:
    """Return LLM-ready business context for ``question`` from local ChromaDB."""
    return format_context(
        retrieve(
            question,
            n_results=n_results,
            persist_dir=persist_dir,
            collection_name=collection_name,
            model_name=model_name,
        )
    )


def main() -> None:
    """Print retrieved context so it can be checked independently of the LLM."""
    configure_utf8_output()
    parser = argparse.ArgumentParser(
        description="Tìm các quy tắc nghiệp vụ liên quan trong ChromaDB cục bộ"
    )
    parser.add_argument("question", nargs="*", help="Câu hỏi nghiệp vụ")
    parser.add_argument("--n-results", type=int, default=3)
    parser.add_argument(
        "--model",
        default=EMBEDDING_MODEL,
        help="Model embedding; phải khớp model đã dùng khi lập chỉ mục",
    )
    arguments = parser.parse_args()
    question = " ".join(arguments.question).strip() or input("Nhập câu hỏi: ").strip()
    if not question:
        raise ValueError("Câu hỏi không được để trống.")

    context = retrieve_context(
        question,
        n_results=arguments.n_results,
        model_name=arguments.model,
    )
    print(context or "Không tìm thấy đoạn ngữ cảnh nào.")


if __name__ == "__main__":
    main()
