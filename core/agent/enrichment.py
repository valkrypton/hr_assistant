"""Input enrichment — schema loading and building the message handed to the SQL
agent (scope block + full schema + conversation history + question). Pure string
assembly, no LLM or DB state."""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_schema_block() -> str:
    """Load the full ERP schema markdown — small enough (~3k tokens) to inject
    entirely, so no chunking/RAG. Cached; schema.md doesn't change at runtime."""
    schema_path = Path(__file__).parent.parent / "context" / "schema.md"
    return schema_path.read_text() if schema_path.exists() else ""


def build_enriched_input(
    user_input: str,
    rbac_ctx,
    schema_block: str,
    conversation_history: list[dict] | None,
) -> str:
    """Assemble the agent message: access rules, schema, prior turns, question."""
    parts = []
    if rbac_ctx is not None:
        parts.append(f"[Access scope for this request]\n{rbac_ctx.scope_hint()}")
    if schema_block:
        parts.append(f"[Full schema context]\n\n{schema_block}")
    if conversation_history:
        history_lines = []
        for turn in conversation_history:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_lines.append(f"{role}: {turn['content']}")
        parts.append(
            "[Conversation history — earlier turns in this thread]\n" + "\n".join(history_lines)
        )
    parts.append(f"[Question]\n{user_input}")
    return "\n\n".join(parts)
