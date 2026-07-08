"""Role → permission mapping.

Permissions are dot-namespaced action strings. A role holds a set of them;
`core.policies.evaluator.can` checks membership (with optional trailing-`*`
wildcards, e.g. "employee.*"). This is intentionally a plain Python dict — one
obvious module to edit — not a YAML/env loader. A loader can be added later
without changing any `can()` call site.

Actions currently in use:
  data.scope.company     — may read company-wide data (unrestricted roles)
  data.scope.department  — scoped to the requester's own department
  data.scope.team        — scoped to the requester's own team
  sql.execute            — may run the free-form (guarded) SQL tool

Typed tools (added incrementally) declare their own actions here, e.g.
"employee.team.read", "employee.leave.read", "employee.headcount.read".
"""

from core.rbac.roles import Role

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.CTO_CEO: frozenset({"data.scope.company", "sql.execute"}),
    Role.HR_MANAGER: frozenset({"data.scope.company", "sql.execute"}),
    Role.DEPT_HEAD: frozenset({"data.scope.department", "sql.execute"}),
    Role.TEAM_LEAD: frozenset({"data.scope.team", "sql.execute"}),
}
