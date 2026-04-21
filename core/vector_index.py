"""
Vector index — semantic search over free-text ERP content.

Indexes team descriptions (project context) and builds a Chroma vector store
that the agent can query for discovery questions like "who has Sabre API experience?".

Run the nightly reindex via:
    python scripts/reindex.py

The index is persisted to VECTOR_STORE_PATH (default: ./data/chroma).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import sqlalchemy

from core.config import settings

if TYPE_CHECKING:
    from langchain_core.vectorstores import VectorStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Document extraction
# ---------------------------------------------------------------------------

_TEAM_QUERY = """
    SELECT
        t.id,
        t.name        AS team_name,
        t.description AS description,
        t.billable,
        t.is_active
    FROM team t
    WHERE t.description IS NOT NULL
      AND LENGTH(TRIM(t.description)) > 10
"""


def _extract_documents() -> list[dict]:
    """Pull indexable free-text content from the ERP database."""
    engine = sqlalchemy.create_engine(settings.DATABASE_URL)
    docs: list[dict] = []

    with engine.connect() as conn:
        rows = conn.execute(sqlalchemy.text(_TEAM_QUERY)).mappings().all()
        for row in rows:
            docs.append(
                {
                    "id": f"team-{row['id']}",
                    "content": (
                        f"Project: {row['team_name']}\n"
                        f"Description: {row['description']}"
                    ),
                    "metadata": {
                        "source": "team",
                        "team_id": row["id"],
                        "team_name": row["team_name"],
                        "billable": row["billable"],
                        "is_active": row["is_active"],
                    },
                }
            )

    logger.info("Extracted %d documents for indexing.", len(docs))
    return docs


# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------

def _get_embeddings():
    """Return an embedding model based on AI_PROVIDER config."""
    provider = settings.AI_PROVIDER.lower()

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY)

    if provider == "anthropic":
        # Anthropic doesn't have a dedicated embeddings API; fall back to OpenAI
        # if OPENAI_API_KEY is set, otherwise use Ollama.
        if settings.OPENAI_API_KEY:
            from langchain_openai import OpenAIEmbeddings
            return OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY)

    # Default: Ollama local embeddings
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.VECTOR_EMBEDDING_MODEL,
    )


# ---------------------------------------------------------------------------
# Index build / load
# ---------------------------------------------------------------------------

def build_index() -> "VectorStore":
    """Extract documents, embed them, and persist the Chroma index."""
    from langchain_chroma import Chroma
    from langchain_core.documents import Document

    docs = _extract_documents()
    if not docs:
        logger.warning("No documents found to index.")

    lc_docs = [
        Document(page_content=d["content"], metadata=d["metadata"])
        for d in docs
    ]

    embeddings = _get_embeddings()

    # Delete existing collection before rebuild to prevent duplicate accumulation
    # on repeated nightly runs.
    import chromadb
    _client = chromadb.PersistentClient(path=settings.VECTOR_STORE_PATH)
    try:
        _client.delete_collection("hr_erp")
    except Exception:
        pass
    del _client

    store = Chroma.from_documents(
        documents=lc_docs,
        embedding=embeddings,
        persist_directory=settings.VECTOR_STORE_PATH,
        collection_name="hr_erp",
    )
    logger.info(
        "Vector index built with %d documents at %s.",
        len(lc_docs),
        settings.VECTOR_STORE_PATH,
    )
    return store


def load_index() -> "VectorStore":
    """Load an existing persisted Chroma index."""
    from langchain_chroma import Chroma

    return Chroma(
        persist_directory=settings.VECTOR_STORE_PATH,
        embedding_function=_get_embeddings(),
        collection_name="hr_erp",
    )


def search(query: str, k: int = 5) -> list[dict]:
    """Semantic search over the indexed documents.

    Returns a list of dicts with 'content', 'metadata', and 'score'.
    """
    store = load_index()
    results = store.similarity_search_with_score(query, k=k)
    return [
        {
            "content": doc.page_content,
            "metadata": doc.metadata,
            "score": float(score),
        }
        for doc, score in results
    ]
