"""Permission checks — `can(subject, action)` and `scope_for(subject)`."""

from __future__ import annotations

from core.policies.permissions import ROLE_PERMISSIONS
from core.rbac.roles import Role


def _role_of(subject: Role | object) -> Role | None:
    """Accept either a Role or anything carrying a `.role` (RBACContext,
    AgentContext, HRUser-like)."""
    if isinstance(subject, Role):
        return subject
    return getattr(subject, "role", None)


def can(subject: Role | object, action: str) -> bool:
    """True if the subject's role holds `action`. Unknown role or action → False
    (fail closed). Supports trailing-`*` wildcards in the permission set, so a
    role granted "employee.*" satisfies "employee.leave.read"."""
    role = _role_of(subject)
    if role is None:
        return False
    perms = ROLE_PERMISSIONS.get(role, frozenset())
    if action in perms:
        return True
    return any(p.endswith(".*") and action.startswith(p[:-1]) for p in perms)


def scope_for(subject: Role | object) -> str:
    """Data-scope tier for the subject: "company", "department", "team", or
    "none". Derived from the permission set so ROLE_PERMISSIONS stays the single
    source of truth."""
    if can(subject, "data.scope.company"):
        return "company"
    if can(subject, "data.scope.department"):
        return "department"
    if can(subject, "data.scope.team"):
        return "team"
    return "none"
