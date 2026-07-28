"""ERP identity resolution — the only module that reads the ERP's Django auth
tables (auth_user, auth_user_groups, auth_group).

Reads are strictly read-only and touch three indexed columns. Results are
cached for RBAC_CACHE_TTL_SECONDS, which bounds how long a revoked group
membership keeps working.

Fail-closed rules encoded here:
  - an inactive auth_user or an inactive person resolves to None (deny)
  - a missing person resolves to None (deny)
  - a database error propagates; a stale cache entry is NEVER served in its
    place, because an expired cache is exactly when a revocation is most
    likely pending
  - negative results are not cached, so provisioning takes effect at once
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import sqlalchemy

_IDENTITY_SQL = sqlalchemy.text(
    "SELECT p.id AS person_id, p.user_id AS auth_user_id "
    "FROM person p "
    "JOIN auth_user u ON u.id = p.user_id "
    "WHERE p.id = :person_id AND p.is_active AND u.is_active"
)

_GROUPS_SQL = sqlalchemy.text("SELECT group_id FROM auth_user_groups WHERE user_id = :auth_user_id")

_EMAIL_TO_PERSON_SQL = sqlalchemy.text(
    "SELECT p.id AS person_id "
    "FROM person p "
    "JOIN auth_user u ON u.id = p.user_id "
    "WHERE lower(u.email) = lower(:email) AND p.is_active AND u.is_active"
)


@dataclass(frozen=True)
class ErpIdentity:
    person_id: int
    auth_user_id: int
    group_ids: frozenset[int]


class ErpIdentityResolver:
    """Resolves person_id -> ErpIdentity against the ERP, with a TTL cache."""

    def __init__(
        self,
        engine: sqlalchemy.Engine,
        ttl_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[int, tuple[float, ErpIdentity]] = {}

    def by_person_id(self, person_id: int) -> ErpIdentity | None:
        now = self._clock()
        cached = self._cache.get(person_id)
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]

        # Deliberately not wrapped in try/except: an ERP failure must deny,
        # not fall back to `cached`.
        identity = self._fetch(person_id)

        if identity is None:
            self._cache.pop(person_id, None)
            return None
        self._cache[person_id] = (now, identity)
        return identity

    def by_email(self, email: str) -> ErpIdentity | None:
        """Resolve an email to its ErpIdentity — used only at login time.

        Delegates to by_person_id so the login path and every subsequent
        per-request resolve_context() call share the same person_id-keyed
        TTL cache, instead of maintaining a second cache keyed by email.
        """
        with self._engine.connect() as conn:
            row = conn.execute(_EMAIL_TO_PERSON_SQL, {"email": email}).first()
        if row is None:
            return None
        return self.by_person_id(row.person_id)

    def _fetch(self, person_id: int) -> ErpIdentity | None:
        with self._engine.connect() as conn:
            row = conn.execute(_IDENTITY_SQL, {"person_id": person_id}).first()
            if row is None:
                return None
            group_rows = conn.execute(_GROUPS_SQL, {"auth_user_id": row.auth_user_id}).all()
        return ErpIdentity(
            person_id=row.person_id,
            auth_user_id=row.auth_user_id,
            group_ids=frozenset(g.group_id for g in group_rows),
        )
