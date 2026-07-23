"""HRUser lookups shared by the HTTP /query path and the Slack adapter —
previously duplicated inline in api/services/query_service.py and
adapters/slack.py."""

from sqlalchemy.orm import Session

from core.rbac.models import HRUser


class HRUserRepository:
    @staticmethod
    def get_by_slack_user_id(session: Session, slack_user_id: str) -> HRUser | None:
        return session.query(HRUser).filter_by(slack_user_id=slack_user_id, is_active=True).first()
