"""Resolve a Slack user id to an AgentContext.

Consolidates the HRUser lookup that was duplicated in the API query service and
the Slack adapter. `resolve()` is the raising entry point used where a failure
should abort (the API path); `lookup_hr_user()` is the bare query for callers
that need to branch on the result themselves (the Slack adapter posts a
distinct message per failure and measures timing in the same session).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.errors import MissingRole, UserNotRegistered
from core.identity.context import AgentContext
from core.rbac.models import HRUser

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def lookup_hr_user(session: Session, slack_user_id: str) -> HRUser | None:
    """The active HRUser for this Slack id, or None."""
    return session.query(HRUser).filter_by(slack_user_id=slack_user_id, is_active=True).first()


def resolve(session: Session, slack_user_id: str) -> AgentContext:
    """Look up the user and build their AgentContext.

    Raises UserNotRegistered if there is no active HRUser, or MissingRole if the
    user exists but has no role assigned.
    """
    hr_user = lookup_hr_user(session, slack_user_id)
    if hr_user is None:
        raise UserNotRegistered()
    if not hr_user.role:
        raise MissingRole()
    return AgentContext.for_user(hr_user, slack_user_id=slack_user_id)
