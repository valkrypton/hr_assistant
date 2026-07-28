"""
SQLAdmin views — mounted on the FastAPI app in main.py.

Add a new ModelView class here for each model that needs admin UI.
"""

from sqladmin import ModelView
from sqladmin.widgets import BooleanInputWidget

from core.rbac.models import HRUser

# sqladmin 0.28.0's BooleanInputWidget subclasses wtforms.widgets.Input directly
# instead of CheckboxInput, so it never gets a validation_attrs list and crashes
# any create/edit form with a BooleanField. Patch in the same attrs CheckboxInput
# defines. https://github.com/aminalaee/sqladmin (fixed upstream? not as of 0.28.0)
BooleanInputWidget.validation_attrs = ["required", "disabled"]


class HRUserAdmin(ModelView, model=HRUser):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"

    column_list = [
        HRUser.id,
        HRUser.employee_id,
        HRUser.slack_user_id,
        HRUser.is_active,
        HRUser.created_at,
    ]
    column_searchable_list = [HRUser.slack_user_id]
    column_sortable_list = [HRUser.id, HRUser.employee_id, HRUser.created_at]

    form_columns = [
        HRUser.employee_id,
        HRUser.slack_user_id,
        HRUser.is_active,
    ]
