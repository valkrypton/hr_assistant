"""
RBACContext — carries the requesting user's identity and enforces data scope.

Usage
-----
    identity = resolver.by_person_id(person_id)
    level = resolve_access_level(identity.group_ids, hr_id, mgmt_id)
    ctx = RBACContext.for_identity(identity, level)
    answer = query(user_input, rbac_ctx=ctx)

Scope rules:
    UNRESTRICTED -> no row restrictions (HR / Management group members)
    SELF         -> own person row and own person-linked rows only

Forbidden columns (FR-5.8 — never exposed regardless of access level):
    salary, compensation, NIC, bank details, personal phone/email, home
    address, date of birth. Enforced at the SQL layer by sql_guard for every
    caller; also named in the prompt so the model doesn't try.

This module is a thin façade: identity + authorization live in
core.rbac.policy.ScopePolicy and output redaction in core.rbac.redaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.rbac.access import AccessLevel
from core.rbac.erp_identity import ErpIdentity
from core.rbac.policy import ScopePolicy
from core.rbac.redaction import FORBIDDEN_COLUMNS, ForbiddenColumnRedactor

__all__ = ["RBACContext", "FORBIDDEN_COLUMNS"]

# Shared, stateless redactor — the forbidden set is a fixed global rule.
_REDACTOR = ForbiddenColumnRedactor()

_HINT_UNRESTRICTED = "You have company-wide access to employee data."
_HINT_SELF = "You can only see your own records."


@dataclass(frozen=True)
class RBACContext(ScopePolicy):
    """Façade over ScopePolicy adding construction, the advisory scope hint,
    and output redaction."""

    @classmethod
    def for_identity(cls, identity: ErpIdentity, access_level: AccessLevel) -> RBACContext:
        return cls(access_level=access_level, person_id=identity.person_id)

    @classmethod
    def unrestricted(cls) -> RBACContext:
        """Convenience context for company-wide access — used in tests and for
        the shared unauthenticated agent."""
        return cls(access_level=AccessLevel.UNRESTRICTED)

    def scope_hint(self) -> str:
        """One-line, human-readable description of this user's scope.

        ADVISORY ONLY. This string carries no authorization weight — it exists
        so a SELF user understands why their result set is small, not to make
        the model enforce anything. All enforcement is in
        core.rbac.sql_guard.rewrite_sql, which runs at the db.run() call site
        and is therefore immune to prompt injection.
        """
        return _HINT_UNRESTRICTED if self.is_unrestricted else _HINT_SELF

    def strip_forbidden(self, text: str) -> str:
        """Best-effort redaction of forbidden column names from agent output."""
        return _REDACTOR.strip(text)
