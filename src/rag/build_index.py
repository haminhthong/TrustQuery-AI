"""Build a persistent ChromaDB index for local business-rule documents.

The index deliberately contains only the curated Markdown files in
``docs/business_rules``.  It is separate from the Olist DuckDB database and
can be recreated at any time:

    python -m src.rag.build_index

The first run downloads the configured sentence-transformers model.  The
download is performed by SentenceTransformer, not by this module directly.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
BUSINESS_RULES_DIR = ROOT / "docs" / "business_rules"
CHROMA_DIR = ROOT / "data" / "chroma"
COLLECTION_NAME = "olist_business_rules"
# Vietnamese and English questions are both used in the benchmark.
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_BATCH_SIZE = 100
_SECTION_HEADING = re.compile(r"(?m)^##\s+(.+?)\s*$")
_DOCUMENT_HEADING = re.compile(r"(?m)^#\s+(.+?)\s*$")


class RAGDependencyError(RuntimeError):
    """Raised when optional RAG dependencies have not been installed."""


class KnowledgeBaseError(RuntimeError):
    """Raised when the curated local knowledge base cannot be indexed."""


@dataclass(frozen=True)
class RuleChunk:
    """One searchable, source-attributable business-rule passage."""

    source: str
    section: str
    text: str

    @property
    def id(self) -> str:
        """Return a stable Chroma document id for this exact passage."""
        fingerprint = f"{self.source}\0{self.section}\0{self.text}".encode("utf-8")
        return "rule-" + hashlib.sha256(fingerprint).hexdigest()


def _normalise_heading(heading: str) -> str:
    """Make a Markdown heading concise enough for Chroma metadata."""
    return re.sub(r"\s+", " ", heading.replace("#", "").strip())


def _document_title(markdown: str, fallback: str) -> str:
    """Read the first H1 title, falling back to the source filename."""
    match = _DOCUMENT_HEADING.search(markdown)
    return _normalise_heading(match.group(1)) if match else fallback


def chunk_markdown(markdown: str, source: str) -> list[RuleChunk]:
    """Split a Markdown document into semantic sections at level-two headings.

    The document title is retained in every chunk so a retrieved passage still
    makes sense after it has been separated from its source file.  Content
    before the first ``##`` heading is document-level introduction rather than
    an individual definition, so it is deliberately not indexed separately.
    """
    if not isinstance(markdown, str):
        raise TypeError("Nội dung tài liệu nghiệp vụ phải là chuỗi UTF-8.")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Nguồn tài liệu nghiệp vụ không được để trống.")

    cleaned = markdown.strip()
    if not cleaned:
        return []

    source = source.replace("\\", "/")
    title = _document_title(cleaned, Path(source).stem.replace("_", " "))
    headings = list(_SECTION_HEADING.finditer(cleaned))
    chunks: list[RuleChunk] = []

    def add_chunk(section: str, body: str) -> None:
        body = body.strip()
        if not body:
            return
        contextual_text = f"# {title}\n\n## {section}\n\n{body}".strip()
        chunks.append(RuleChunk(source=source, section=section, text=contextual_text))

    if not headings:
        add_chunk("Tổng quan", cleaned)
        return chunks

    for index, heading in enumerate(headings):
        section = _normalise_heading(heading.group(1)) or "Tổng quan"
        body_start = heading.end()
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(cleaned)
        add_chunk(section, cleaned[body_start:body_end])

    return chunks


def load_rule_chunks(documents_dir: Path = BUSINESS_RULES_DIR) -> list[RuleChunk]:
    """Load all curated business-rule Markdown files in deterministic order."""
    documents_dir = Path(documents_dir)
    if not documents_dir.is_dir():
        raise KnowledgeBaseError(
            f"Không tìm thấy thư mục quy tắc nghiệp vụ: {documents_dir}"
        )

    files = sorted(
        (path for path in documents_dir.rglob("*.md") if path.is_file()),
        key=lambda path: path.relative_to(documents_dir).as_posix().lower(),
    )
    if not files:
        raise KnowledgeBaseError(
            f"Không có tệp Markdown nào trong thư mục: {documents_dir}"
        )

    chunks: list[RuleChunk] = []
    for path in files:
        try:
            markdown = path.read_text(encoding="utf-8")
        except OSError as error:
            raise KnowledgeBaseError(f"Không đọc được tài liệu: {path}") from error
        source = path.relative_to(documents_dir).as_posix()
        chunks.extend(chunk_markdown(markdown, source))

    if not chunks:
        raise KnowledgeBaseError("Các tài liệu nghiệp vụ không chứa nội dung để lập chỉ mục.")
    return chunks


@lru_cache(maxsize=4)
def load_embedding_model(
    model_name: str = EMBEDDING_MODEL, *, local_files_only: bool = False
) -> Any:
    """Load the sentence-transformers model only when an embedding is needed.

    Index creation permits the initial model download.  Retrieval can set
    ``local_files_only`` because the same model must already have been present
    to create a usable local index.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise RAGDependencyError(
            "Thiếu sentence-transformers. Hãy chạy: "
            "python -m pip install -r requirements.txt"
        ) from error
    return SentenceTransformer(model_name, local_files_only=local_files_only)


def embed_texts(model: Any, texts: Sequence[str]) -> list[list[float]]:
    """Embed texts explicitly instead of relying on Chroma's default model."""
    if not texts:
        return []
    vectors = model.encode(
        list(texts),
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist() if hasattr(vectors, "tolist") else [list(vector) for vector in vectors]


def get_chroma_client(persist_dir: Path = CHROMA_DIR) -> Any:
    """Create Chroma's local persistent client without opening any remote service."""
    try:
        import chromadb
    except ImportError as error:
        raise RAGDependencyError(
            "Thiếu chromadb. Hãy chạy: python -m pip install -r requirements.txt"
        ) from error
    return chromadb.PersistentClient(path=str(Path(persist_dir)))


def _collection_exists(client: Any, collection_name: str) -> bool:
    """Support Chroma list_collections return shapes across supported releases."""
    collections = client.list_collections()
    return any(
        (collection if isinstance(collection, str) else collection.name) == collection_name
        for collection in collections
    )


def _batched(values: Sequence[RuleChunk], size: int = _BATCH_SIZE) -> Iterable[Sequence[RuleChunk]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def build_index(
    documents_dir: Path = BUSINESS_RULES_DIR,
    persist_dir: Path = CHROMA_DIR,
    collection_name: str = COLLECTION_NAME,
    model_name: str = EMBEDDING_MODEL,
    *,
    local_files_only: bool = False,
) -> int:
    """Rebuild and persist the curated business-rule collection.

    Rebuilding removes only the named generated Chroma collection.  It never
    reads or changes the Olist database or raw data.  Returning the indexed
    chunk count makes this function straightforward to smoke-test and script.
    """
    chunks = load_rule_chunks(documents_dir)
    model = load_embedding_model(model_name, local_files_only=local_files_only)
    client = get_chroma_client(persist_dir)

    if _collection_exists(client, collection_name):
        client.delete_collection(collection_name)
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={
            "hnsw:space": "cosine",
            "purpose": "olist_business_rules",
            "embedding_model": model_name,
        },
    )

    for batch in _batched(chunks):
        documents = [chunk.text for chunk in batch]
        collection.upsert(
            ids=[chunk.id for chunk in batch],
            documents=documents,
            embeddings=embed_texts(model, documents),
            metadatas=[
                {"source": chunk.source, "section": chunk.section}
                for chunk in batch
            ],
        )
    return len(chunks)


def main() -> None:
    """Build the local index from the command line."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Lập chỉ mục ChromaDB cục bộ từ docs/business_rules/*.md"
    )
    parser.add_argument(
        "--documents-dir",
        type=Path,
        default=BUSINESS_RULES_DIR,
        help="Thư mục Markdown chứa quy tắc nghiệp vụ",
    )
    parser.add_argument(
        "--persist-dir",
        type=Path,
        default=CHROMA_DIR,
        help="Thư mục lưu ChromaDB cục bộ",
    )
    parser.add_argument(
        "--model",
        default=EMBEDDING_MODEL,
        help="Tên mô hình sentence-transformers dùng để tạo embedding",
    )
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="Chỉ dùng model đã cache; hữu ích khi lập lại chỉ mục ngoại tuyến",
    )
    arguments = parser.parse_args()
    count = build_index(
        documents_dir=arguments.documents_dir,
        persist_dir=arguments.persist_dir,
        model_name=arguments.model,
        local_files_only=arguments.local_files_only,
    )
    print(f"Đã lập chỉ mục {count} đoạn quy tắc nghiệp vụ tại {arguments.persist_dir}.")


if __name__ == "__main__":
    main()
