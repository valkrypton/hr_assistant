"""
SQLAdmin views — mounted on the FastAPI app in main.py.

Add a new ModelView class here for each model that needs admin UI.
"""
from sqladmin import ModelView
from wtforms import SelectField

from core.rbac.models import HRUser
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
