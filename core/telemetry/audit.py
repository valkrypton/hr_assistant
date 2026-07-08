"""
Audit-log writer (FR-6.1 / FR-6.2).

Single append-only writer shared by the API layer and the messaging adapters.
The signature is a superset of both former call sites: the plain-HTTP path
passes the core latency/token fields; the Slack path additionally passes the
Slack-specific timing breakdown. All fields beyond `question` are optional.
"""

from sqlalchemy.orm import Session

from core.rbac.models import AuditLog


def write_audit(
    session: Session,
    *,
    slack_user_id: str | None,
    employee_id: int | None,
    role: str | None,
    question: str,
    answer: str | None = None,
    tables_accessed: str | None = None,
    error: str | None = None,
    schema_rag_ms: int | None = None,
    agent_ms: int | None = None,
    total_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    user_lookup_ms: int | None = None,
    rate_check_ms: int | None = None,
    history_fetch_ms: int | None = None,
    slack_post_ms: int | None = None,
) -> None:
    """Append one row to the audit log in the app DB."""
    session.add(
        AuditLog(
            slack_user_id=slack_user_id,
            employee_id=employee_id,
            role=role,
            question=question,
            answer=answer,
            tables_accessed=tables_accessed,
            error=error,
            schema_rag_ms=schema_rag_ms,
            agent_ms=agent_ms,
            total_ms=total_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            user_lookup_ms=user_lookup_ms,
            rate_check_ms=rate_check_ms,
            history_fetch_ms=history_fetch_ms,
            slack_post_ms=slack_post_ms,
        )
    )
    session.commit()
