"""
Shared rate-limit counting logic.

Lives in core/ (not api/) so both api.deps.check_rate_limit and
adapters.slack.process_event can share one implementation without
adapters/ importing from api/ (see AGENTS.md import rules: api imports
adapters, never the reverse).

This module only counts — deciding what to do with the count (raise an
HTTPException vs. post a Slack message) stays with each caller.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from core.rbac.models import AuditLog


def count_recent_queries(session: Session, slack_user_id: str) -> int:
    """Return how many AuditLog rows slack_user_id has in the last hour."""
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    return (
        session.query(AuditLog)
        .filter(
            AuditLog.slack_user_id == slack_user_id,
            AuditLog.created_at >= since,
        )
        .count()
    )
