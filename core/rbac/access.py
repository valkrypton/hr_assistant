"""Access levels derived from ERP Django group membership.

Two levels only — see docs/superpowers/specs/2026-07-27-deterministic-rbac-design.md.
Membership in the HR group ("Pod") or the Management group grants company-wide
access; everyone else is restricted to their own records.

Group IDs are configured, never names: the ERP has 28 groups whose names match
"pod" or "manage" (Advanced POD Permissions, POD Reminder Group, Management
Permissions, Leave Management, ...), so any fuzzy match would grant
unrestricted access to the wrong population. Name drift is caught at boot by
assert_rbac_groups_exist in core/rbac/erp_identity.py, not here.
"""

from __future__ import annotations

from enum import Enum


class AccessLevel(str, Enum):
    UNRESTRICTED = "unrestricted"  # member of the HR or Management group
    SELF = "self"  # everyone else — own records only


def resolve_access_level(
    group_ids: frozenset[int],
    hr_group_id: int,
    management_group_id: int,
) -> AccessLevel:
    """The entire authorization decision. Pure — no I/O, no settings lookup."""
    if hr_group_id in group_ids or management_group_id in group_ids:
        return AccessLevel.UNRESTRICTED
    return AccessLevel.SELF
