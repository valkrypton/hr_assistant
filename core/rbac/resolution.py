"""Composition root for RBAC — turns a person_id into an RBACContext.

Lives in core/ (not api/) because adapters/slack.py needs it too and adapters/
cannot import from api/ — the same constraint that put the engines in
core/db.py.
"""

from __future__ import annotations

from functools import lru_cache

from core.config import settings
from core.db import erp_engine
from core.rbac.access import resolve_access_level
from core.rbac.context import RBACContext
from core.rbac.erp_identity import ErpIdentityResolver


@lru_cache(maxsize=1)
def get_resolver() -> ErpIdentityResolver:
    """Process-wide resolver so its TTL cache is shared across requests."""
    return ErpIdentityResolver(erp_engine(), ttl_seconds=settings.RBAC_CACHE_TTL_SECONDS)


def resolve_context(
    person_id: int, resolver: ErpIdentityResolver | None = None
) -> RBACContext | None:
    """Resolve a person id to its RBAC context, or None when the ERP has no
    active person/auth_user for it (caller must deny).

    Raises whatever the ERP raises on failure — deliberately. A dead ERP
    denies; it never falls back to a wider scope.
    """
    active = resolver if resolver is not None else get_resolver()
    identity = active.by_person_id(person_id)
    if identity is None:
        return None
    level = resolve_access_level(
        identity.group_ids,
        settings.HR_GROUP_ID,
        settings.MANAGEMENT_GROUP_ID,
    )
    return RBACContext.for_identity(identity, level)
