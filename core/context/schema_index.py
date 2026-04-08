"""
Schema RAG — chunks schema.md into sections and stores them in a Chroma
collection so the agent receives only the relevant parts per query.

Collections:
  hr_schema   — schema.md sections (this module)
  hr_erp      — ERP free-text content (core/vector_index.py)
"""

from __future__ import annotations

import re
from pathlib import Path

from core.config import settings

_SCHEMA_PATH = Path(__file__).parent / "schema.md"
_COLLECTION = "hr_schema"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_schema() -> list[dict]:
    """
    Split schema.md into chunks at every ## or ### heading.
    Each chunk carries:
      - text       : heading + body (what gets embedded and returned)
      - section    : the ## heading it belongs to
      - subsection : the ### heading (empty string for ## level chunks)
    """
    text = _SCHEMA_PATH.read_text()
    # Split at every ## or ### heading (keep the delimiter)
    parts = re.split(r"(?=^#{2,3} )", text, flags=re.MULTILINE)

    chunks: list[dict] = []
    current_section = ""

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if part.startswith("### "):
            lines = part.splitlines()
            subsection = lines[0].lstrip("# ").strip()
            chunks.append(
                {
                    "text": part,
                    "section": current_section,
                    "subsection": subsection,
                }
            )
        elif part.startswith("## "):
            lines = part.splitlines()
            current_section = lines[0].lstrip("# ").strip()
            chunks.append(
                {
                    "text": part,
                    "section": current_section,
                    "subsection": "",
                }
            )

    return chunks


# ---------------------------------------------------------------------------
# Embeddings (shared helper)
# ---------------------------------------------------------------------------

def _get_embeddings():
    """Return an embedding model consistent with core/vector_index.py."""
    provider = settings.AI_PROVIDER.lower()

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY)

    if provider == "anthropic" and settings.OPENAI_API_KEY:
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY)

    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.VECTOR_EMBEDDING_MODEL,
    )


# ---------------------------------------------------------------------------
# Build / load
# ---------------------------------------------------------------------------

def build_schema_index() -> None:
    """Chunk schema.md, embed all sections, persist to Chroma."""
    from langchain_chroma import Chroma
    from langchain_core.documents import Document

    chunks = _chunk_schema()
    docs = [
        Document(
            page_content=c["text"],
            metadata={"section": c["section"], "subsection": c["subsection"]},
        )
        for c in chunks
    ]

    Chroma.from_documents(
        documents=docs,
        embedding=_get_embeddings(),
        persist_directory=settings.VECTOR_STORE_PATH,
        collection_name=_COLLECTION,
    )


def _load_schema_index():
    from langchain_chroma import Chroma
    return Chroma(
        persist_directory=settings.VECTOR_STORE_PATH,
        embedding_function=_get_embeddings(),
        collection_name=_COLLECTION,
    )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def retrieve(query: str, k: int = 4) -> list[str]:
    """
    Return the top-k most relevant schema sections for a given query.
    Falls back to an empty list if the index hasn't been built yet.
    """
    try:
        store = _load_schema_index()
        results = store.similarity_search(query, k=k)
        return [doc.page_content for doc in results]
    except Exception:
        return []
