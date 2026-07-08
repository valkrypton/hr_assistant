"""
Shared rate-limit counting logic.

Lives in core/ so the execution pipeline and the Slack adapter can share one
implementation without adapters/ importing from api/ (see AGENTS.md import
rules: api imports adapters, never the reverse).

count_recent_queries only counts; enforce_rate_limit raises RateLimitExceeded.
The Slack adapter counts directly (it posts a friendly message and measures
timing) instead of using enforce_rate_limit.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from core.config import settings
from core.errors import RateLimitExceeded
from core.rbac.models import AuditLog


def count_recent_queries(session: Session, slack_user_id: str) -> int:
    """Return how many AuditLog rows slack_user_id has in the last hour."""
    since = datetime.now(UTC) - timedelta(hours=1)
    return (
        session.query(AuditLog)
        .filter(
            AuditLog.slack_user_id == slack_user_id,
            AuditLog.created_at >= since,
        )
        .count()
    )


def enforce_rate_limit(session: Session, slack_user_id: str) -> None:
    """Raise RateLimitExceeded if the user has hit RATE_LIMIT_PER_HOUR queries in
    the last hour. RATE_LIMIT_PER_HOUR=0 disables the limit. The framework-neutral
    counterpart to api.deps.check_rate_limit (which raises an HTTPException)."""
    limit = settings.RATE_LIMIT_PER_HOUR
    if limit <= 0:
        return
    if count_recent_queries(session, slack_user_id) >= limit:
        raise RateLimitExceeded(
            f"Rate limit exceeded — max {limit} queries per hour. Try again later."
        )
