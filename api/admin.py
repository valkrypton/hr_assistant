"""
SQLAdmin views — mounted on the FastAPI app in main.py.

Add a new ModelView class here for each model that needs admin UI.
"""
from sqladmin import ModelView
from wtforms import SelectField

from core.rbac.models import HRUser, AuditLog
from core.rbac.roles import Role

_ROLE_CHOICES = [(r.value, r.value.replace("_", " ").title()) for r in Role]


class HRUserAdmin(ModelView, model=HRUser):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"

    column_list = [
        HRUser.id,
        HRUser.employee_id,
        HRUser.role,
        HRUser.slack_user_id,
        HRUser.department_id,
        HRUser.team_id,
        HRUser.is_active,
        HRUser.created_at,
    ]
    column_searchable_list = [HRUser.slack_user_id, HRUser.role]
    column_sortable_list = [HRUser.id, HRUser.employee_id, HRUser.role, HRUser.created_at]

    form_columns = [
        HRUser.employee_id,
        HRUser.role,
        HRUser.slack_user_id,
        HRUser.department_id,
        HRUser.team_id,
        HRUser.is_active,
    ]

    form_overrides = {"role": SelectField}
    form_args = {"role": {"choices": _ROLE_CHOICES}}


class AuditLogAdmin(ModelView, model=AuditLog):
    name = "Audit Log"
    name_plural = "Audit Logs"
    icon = "fa-solid fa-clipboard-list"

    # Read-only — no create/edit/delete in the admin UI.
    can_create = False
    can_edit = False
    can_delete = False

    column_list = [
        AuditLog.id,
        AuditLog.created_at,
        AuditLog.slack_user_id,
        AuditLog.role,
        AuditLog.question,
        AuditLog.tables_accessed,
        AuditLog.total_ms,
        AuditLog.error,
    ]
    column_sortable_list = [AuditLog.id, AuditLog.created_at, AuditLog.role, AuditLog.total_ms]
    column_searchable_list = [AuditLog.slack_user_id, AuditLog.question]

    column_details_list = [
        AuditLog.id,
        AuditLog.created_at,
        AuditLog.slack_user_id,
        AuditLog.employee_id,
        AuditLog.role,
        AuditLog.question,
        AuditLog.answer,
        AuditLog.tables_accessed,
        AuditLog.user_lookup_ms,
        AuditLog.rate_check_ms,
        AuditLog.history_fetch_ms,
        AuditLog.schema_rag_ms,
        AuditLog.agent_ms,
        AuditLog.slack_post_ms,
        AuditLog.total_ms,
        AuditLog.prompt_tokens,
        AuditLog.completion_tokens,
        AuditLog.total_tokens,
        AuditLog.error,
    ]
