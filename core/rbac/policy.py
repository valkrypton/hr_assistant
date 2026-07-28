"""ScopePolicy — the requester's identity plus the single authorization check
(is_unrestricted). No prompt strings, no regex — SRP.

Two access levels only; see core/rbac/access.py. The requester's person_id is
the whole of their scope when restricted, so there are no department/team
fields left to carry.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.rbac.access import AccessLevel


@dataclass(frozen=True)
class ScopePolicy:
    access_level: AccessLevel
    person_id: int | None = None  # the requester's own person.id

    @property
    def is_unrestricted(self) -> bool:
        """True for company-wide access (HR / Management group members)."""
        return self.access_level is AccessLevel.UNRESTRICTED
