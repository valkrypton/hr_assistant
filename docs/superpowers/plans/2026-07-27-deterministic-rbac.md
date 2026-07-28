# Deterministic RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four-role, LLM-prompt-enforced RBAC with two access levels derived deterministically from ERP Django group membership, leaving `sql_guard` as the sole enforcement point.

**Architecture:** At request time the requester's `person.id` is resolved against the ERP's `auth_user` / `auth_user_groups` tables. Membership in the HR (`Pod`) or `Management` group yields `AccessLevel.UNRESTRICTED`; everyone else gets `AccessLevel.SELF`, whose SQL is rewritten to `person_id = <requester>` before execution. The scope prompt is demoted to a one-line advisory hint with no authorization weight.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, sqlglot, Alembic, pytest, `uv` for all dependency and command execution.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-27-deterministic-rbac-design.md`. Read it before Task 1.
- All commands run through `uv` (`uv run pytest`, `uv run alembic`). Never `pip` or a bare `python`.
- `pre-commit` runs ruff lint + format on commit. Never bypass with `--no-verify`.
- Every authorization failure denies. No code path may degrade to wider access on error.
- `sql_guard`'s existing protections are load-bearing and must not be weakened: DML/DDL blocking, `_BLOCKED_FUNCTIONS`, `SELECT ... INTO` rejection, forbidden-column blocking, wildcard/whole-row rejection, `assert_tables_classified`, `assert_person_free_tables_have_no_person_fk`, and `_inject_and`'s paren-wrapping.
- Salary/CNIC/DOB stay in `FORBIDDEN_COLUMNS` for **every** access level, including UNRESTRICTED.
- Migration files follow `NNNN_snake_case.py`, enforced by `scripts/check_migration_naming.sh`.
- Per `CLAUDE.md`, this is security work — but test authoring and test runs are delegated to a Sonnet/Haiku agent rather than run in a Fable session.

## Scope Note — read before starting

The spec describes resolution from an **SSO email**. There is no SSO login in this codebase today. The actual entry points are:

- **Slack** (`adapters/slack.py`): `slack_user_id` → `hr_assistant_users` row → `employee_id`.
- **HTTP `POST /query`**: admin Basic Auth, with `slack_user_id` in the request body.

`HRUser.employee_id` **is** `person.id` (see `core/rbac/models.py:59-61` and the existing `ScopePolicy.employee_id` docstring). So this plan resolves identity by `person_id`, not by email. `ErpIdentityResolver` gains no email method — YAGNI; add one in the SSO plan when SSO exists.

`hr_assistant_users` therefore survives as the Slack-ID mapping table. It loses `role`, `department_id`, `team_id` only.

Also note: `ScopePolicy.can_see_employee` has **no production callers** — only `tests/test_rbac.py`. It is deleted, not ported.

---

### Task 1: AccessLevel and config

**Files:**
- Create: `core/rbac/access.py`
- Modify: `core/config.py:85` (after `INCLUDED_TABLES`)
- Test: `tests/test_access_level.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AccessLevel` (str Enum, members `UNRESTRICTED`/`SELF`); `resolve_access_level(group_ids: frozenset[int], hr_group_id: int, management_group_id: int) -> AccessLevel`; settings fields `HR_GROUP_ID: int`, `HR_GROUP_NAME: str`, `MANAGEMENT_GROUP_ID: int`, `MANAGEMENT_GROUP_NAME: str`, `RBAC_CACHE_TTL_SECONDS: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_access_level.py`:

```python
"""AccessLevel resolution — pure function, no I/O."""

import pytest

from core.rbac.access import AccessLevel, resolve_access_level

HR = 12
MGMT = 13


@pytest.mark.parametrize(
    "group_ids,expected",
    [
        (frozenset({HR}), AccessLevel.UNRESTRICTED),
        (frozenset({MGMT}), AccessLevel.UNRESTRICTED),
        (frozenset({HR, MGMT}), AccessLevel.UNRESTRICTED),
        (frozenset({HR, 99, 100}), AccessLevel.UNRESTRICTED),
        (frozenset(), AccessLevel.SELF),
        (frozenset({99}), AccessLevel.SELF),
    ],
)
def test_resolve_access_level(group_ids, expected):
    assert resolve_access_level(group_ids, HR, MGMT) is expected


def test_adjacent_group_ids_do_not_grant_unrestricted():
    """Guards the name-collision hazard from the spec: 'Advanced POD
    Permissions' (id 9) is NOT 'Pod' (id 12)."""
    assert resolve_access_level(frozenset({9, 10, 11}), HR, MGMT) is AccessLevel.SELF


def test_access_level_values_are_stable_strings():
    assert AccessLevel.UNRESTRICTED.value == "unrestricted"
    assert AccessLevel.SELF.value == "self"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_access_level.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.rbac.access'`

- [ ] **Step 3: Write the implementation**

Create `core/rbac/access.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_access_level.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Add the config fields**

In `core/config.py`, immediately after the `INCLUDED_TABLES` field declaration (line 85) and before the `ERP_POOL_SIZE` comment block, insert:

```python
    # ERP Django auth group IDs that grant company-wide bot access.
    # IDs, not names — see core/rbac/access.py for why. Verified at boot
    # against HR_GROUP_NAME / MANAGEMENT_GROUP_NAME so a rename in the ERP
    # fails the deploy instead of silently regranting access.
    HR_GROUP_ID: int = 12
    HR_GROUP_NAME: str = "Pod"
    MANAGEMENT_GROUP_ID: int = 13
    MANAGEMENT_GROUP_NAME: str = "Management"

    # How long a resolved ERP identity + access level stays cached.
    # Bounds the window in which a revoked group membership still works.
    RBAC_CACHE_TTL_SECONDS: int = 900
```

- [ ] **Step 6: Write the config test**

Append to `tests/test_config_guards.py`:

```python
def test_rbac_group_defaults():
    from core.config import settings

    assert settings.HR_GROUP_ID == 12
    assert settings.HR_GROUP_NAME == "Pod"
    assert settings.MANAGEMENT_GROUP_ID == 13
    assert settings.MANAGEMENT_GROUP_NAME == "Management"
    assert settings.RBAC_CACHE_TTL_SECONDS == 900
```

- [ ] **Step 7: Run both test files**

Run: `uv run pytest tests/test_access_level.py tests/test_config_guards.py -v`
Expected: PASS

- [ ] **Step 8: Document the new env vars**

In `README.md`, in the env-var table that currently documents `INCLUDED_TABLES` (line 68), add four rows:

```markdown
| `HR_GROUP_ID` | ERP `auth_group.id` of the HR group (default 12, "Pod") — members get company-wide access |
| `MANAGEMENT_GROUP_ID` | ERP `auth_group.id` of the Management group (default 13) — members get company-wide access |
| `HR_GROUP_NAME` / `MANAGEMENT_GROUP_NAME` | Expected names for the IDs above; a mismatch fails startup |
| `RBAC_CACHE_TTL_SECONDS` | How long a resolved access level is cached (default 900) |
```

- [ ] **Step 9: Commit**

```bash
git add core/rbac/access.py core/config.py tests/test_access_level.py tests/test_config_guards.py README.md
git commit -m "feat(rbac): add AccessLevel and ERP group config"
```

---

### Task 2: ERP identity resolver

**Files:**
- Create: `core/rbac/erp_identity.py`
- Test: `tests/test_erp_identity.py`

**Interfaces:**
- Consumes: `AccessLevel`, `resolve_access_level` from Task 1.
- Produces: `ErpIdentity` frozen dataclass with fields `person_id: int`, `auth_user_id: int`, `group_ids: frozenset[int]`; `ErpIdentityResolver(engine, ttl_seconds: int, clock: Callable[[], float])` with method `by_person_id(person_id: int) -> ErpIdentity | None`; `assert_rbac_groups_exist(engine, expected: dict[int, str]) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_erp_identity.py`:

```python
"""ErpIdentityResolver — the only module that reads ERP auth tables."""

import pytest
import sqlalchemy

from core.rbac.erp_identity import (
    ErpIdentity,
    ErpIdentityResolver,
    assert_rbac_groups_exist,
)

HR = 12
MGMT = 13


@pytest.fixture
def erp_engine():
    """Minimal in-memory stand-in for the ERP auth tables.

    StaticPool + a shared connection keeps the same in-memory DB alive across
    the engine's checkouts; without it each connection sees an empty database.
    """
    engine = sqlalchemy.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text(
                "CREATE TABLE auth_user (id INTEGER PRIMARY KEY, is_active INTEGER NOT NULL)"
            )
        )
        conn.execute(
            sqlalchemy.text(
                "CREATE TABLE person (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
                "is_active INTEGER NOT NULL)"
            )
        )
        conn.execute(
            sqlalchemy.text(
                "CREATE TABLE auth_user_groups (id INTEGER PRIMARY KEY, "
                "user_id INTEGER NOT NULL, group_id INTEGER NOT NULL)"
            )
        )
        conn.execute(
            sqlalchemy.text("CREATE TABLE auth_group (id INTEGER PRIMARY KEY, name VARCHAR(150))")
        )
        conn.execute(sqlalchemy.text("INSERT INTO auth_user VALUES (500, 1), (501, 1), (502, 0)"))
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO person VALUES (100, 500, 1), (101, 501, 1), "
                "(102, 502, 1), (103, 500, 0)"
            )
        )
        # person 100 -> HR group; person 101 -> no groups
        conn.execute(sqlalchemy.text("INSERT INTO auth_user_groups VALUES (1, 500, 12), (2, 500, 9)"))
        conn.execute(sqlalchemy.text("INSERT INTO auth_group VALUES (12, 'Pod'), (13, 'Management')"))
    return engine


def test_resolves_person_with_groups(erp_engine):
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    identity = resolver.by_person_id(100)
    assert identity == ErpIdentity(person_id=100, auth_user_id=500, group_ids=frozenset({12, 9}))


def test_resolves_person_with_no_groups(erp_engine):
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    identity = resolver.by_person_id(101)
    assert identity == ErpIdentity(person_id=101, auth_user_id=501, group_ids=frozenset())


def test_unknown_person_returns_none(erp_engine):
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    assert resolver.by_person_id(999) is None


def test_inactive_auth_user_returns_none(erp_engine):
    """person 102 -> auth_user 502, is_active = 0."""
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    assert resolver.by_person_id(102) is None


def test_inactive_person_returns_none(erp_engine):
    """person 103 is_active = 0."""
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    assert resolver.by_person_id(103) is None


def test_cache_hit_inside_ttl_does_not_requery(erp_engine):
    clock = iter([0.0, 10.0])
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900, clock=lambda: next(clock))
    first = resolver.by_person_id(100)
    with erp_engine.begin() as conn:
        conn.execute(sqlalchemy.text("DELETE FROM auth_user_groups WHERE user_id = 500"))
    second = resolver.by_person_id(100)
    assert second == first
    assert second.group_ids == frozenset({12, 9})


def test_cache_refetches_after_ttl(erp_engine):
    clock = iter([0.0, 1000.0, 1000.0])
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900, clock=lambda: next(clock))
    resolver.by_person_id(100)
    with erp_engine.begin() as conn:
        conn.execute(sqlalchemy.text("DELETE FROM auth_user_groups WHERE user_id = 500"))
    assert resolver.by_person_id(100).group_ids == frozenset()


def test_erp_error_propagates_and_does_not_serve_stale(erp_engine):
    """A dead ERP must deny, never fall back to a cached identity."""
    clock = iter([0.0, 1000.0])
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900, clock=lambda: next(clock))
    resolver.by_person_id(100)
    with erp_engine.begin() as conn:
        conn.execute(sqlalchemy.text("DROP TABLE person"))
    with pytest.raises(sqlalchemy.exc.SQLAlchemyError):
        resolver.by_person_id(100)


def test_negative_result_is_not_cached(erp_engine):
    """A user provisioned after a miss must work immediately."""
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    assert resolver.by_person_id(200) is None
    with erp_engine.begin() as conn:
        conn.execute(sqlalchemy.text("INSERT INTO auth_user VALUES (600, 1)"))
        conn.execute(sqlalchemy.text("INSERT INTO person VALUES (200, 600, 1)"))
    assert resolver.by_person_id(200) is not None


def test_group_assertion_passes_when_names_match(erp_engine):
    assert_rbac_groups_exist(erp_engine, {HR: "Pod", MGMT: "Management"})


def test_group_assertion_fails_on_rename(erp_engine):
    with erp_engine.begin() as conn:
        conn.execute(sqlalchemy.text("UPDATE auth_group SET name = 'People Ops' WHERE id = 12"))
    with pytest.raises(RuntimeError, match="People Ops"):
        assert_rbac_groups_exist(erp_engine, {HR: "Pod", MGMT: "Management"})


def test_group_assertion_fails_on_missing_group(erp_engine):
    with erp_engine.begin() as conn:
        conn.execute(sqlalchemy.text("DELETE FROM auth_group WHERE id = 13"))
    with pytest.raises(RuntimeError, match="13"):
        assert_rbac_groups_exist(erp_engine, {HR: "Pod", MGMT: "Management"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_erp_identity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.rbac.erp_identity'`

- [ ] **Step 3: Write the implementation**

Create `core/rbac/erp_identity.py`:

```python
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

_GROUP_NAMES_SQL = sqlalchemy.text("SELECT id, name FROM auth_group WHERE id IN :ids")


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


def assert_rbac_groups_exist(engine: sqlalchemy.Engine, expected: dict[int, str]) -> None:
    """Boot-time check that the configured group IDs still exist and still
    carry the expected names.

    Same fail-closed posture as sql_guard.assert_tables_classified: a rename or
    deletion in the ERP breaks the deploy rather than silently changing who has
    company-wide access.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            _GROUP_NAMES_SQL.bindparams(sqlalchemy.bindparam("ids", expanding=True)),
            {"ids": list(expected)},
        ).all()
    found = {r.id: r.name for r in rows}

    problems = []
    for group_id, expected_name in expected.items():
        actual = found.get(group_id)
        if actual is None:
            problems.append(f"group id {group_id} (expected name {expected_name!r}) does not exist")
        elif actual != expected_name:
            problems.append(
                f"group id {group_id} is named {actual!r}, expected {expected_name!r}"
            )
    if problems:
        raise RuntimeError(
            "ERP RBAC group check failed: "
            + "; ".join(problems)
            + ". Update HR_GROUP_ID/MANAGEMENT_GROUP_ID (and their _NAME "
            "counterparts) to match the ERP before deploying."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_erp_identity.py -v`
Expected: PASS, 12 passed

- [ ] **Step 5: Commit**

```bash
git add core/rbac/erp_identity.py tests/test_erp_identity.py
git commit -m "feat(rbac): resolve ERP identity and group membership"
```

---

### Task 3: Two-level ScopePolicy and RBACContext

**Files:**
- Modify: `core/rbac/policy.py` (full rewrite, 32 lines → ~30)
- Modify: `core/rbac/context.py:31-71`
- Test: `tests/test_scope_policy.py`

**Interfaces:**
- Consumes: `AccessLevel` (Task 1), `ErpIdentity` (Task 2).
- Produces: `ScopePolicy(access_level: AccessLevel, person_id: int | None = None)` with property `is_unrestricted -> bool`; `RBACContext.for_identity(identity: ErpIdentity, access_level: AccessLevel) -> RBACContext`; `RBACContext.unrestricted() -> RBACContext`; `RBACContext.scope_hint() -> str`; `RBACContext.strip_forbidden(text: str) -> str`.

`ScopePolicy.role`, `ScopePolicy.employee_id`, `ScopePolicy.department_id`, `ScopePolicy.team_id`, `can_see_employee`, `RBACContext.for_user`, and `RBACContext.superuser` all cease to exist.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scope_policy.py`:

```python
"""ScopePolicy / RBACContext — two access levels, no roles."""

import pytest

from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext
from core.rbac.erp_identity import ErpIdentity
from core.rbac.policy import ScopePolicy


def test_unrestricted_is_unrestricted():
    assert ScopePolicy(access_level=AccessLevel.UNRESTRICTED).is_unrestricted is True


def test_self_is_restricted():
    assert ScopePolicy(access_level=AccessLevel.SELF, person_id=7).is_unrestricted is False


def test_policy_is_frozen():
    policy = ScopePolicy(access_level=AccessLevel.SELF, person_id=7)
    with pytest.raises(Exception):
        policy.person_id = 8


def test_for_identity_carries_person_id():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset({12}))
    ctx = RBACContext.for_identity(identity, AccessLevel.UNRESTRICTED)
    assert ctx.person_id == 42
    assert ctx.is_unrestricted is True


def test_for_identity_self_level():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset())
    ctx = RBACContext.for_identity(identity, AccessLevel.SELF)
    assert ctx.is_unrestricted is False
    assert ctx.person_id == 42


def test_unrestricted_helper():
    assert RBACContext.unrestricted().is_unrestricted is True


def test_scope_hint_mentions_own_records_for_self():
    ctx = RBACContext(access_level=AccessLevel.SELF, person_id=42)
    assert "own records" in ctx.scope_hint().lower()


def test_scope_hint_mentions_company_wide_for_unrestricted():
    assert "company-wide" in RBACContext.unrestricted().scope_hint().lower()


def test_strip_forbidden_still_redacts():
    ctx = RBACContext.unrestricted()
    assert "REDACTED" in ctx.strip_forbidden("salary: 100000")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scope_policy.py -v`
Expected: FAIL — `ImportError: cannot import name 'AccessLevel'` from the policy module, and `AttributeError` on `for_identity`

- [ ] **Step 3: Rewrite `core/rbac/policy.py`**

Replace the entire file with:

```python
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
```

- [ ] **Step 4: Rewrite `core/rbac/context.py`**

Replace the entire file with:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_scope_policy.py -v`
Expected: PASS, 9 passed

Note: `tests/test_rbac.py` is now heavily broken. That is expected and is cleaned up in Task 8 — do not fix it here.

- [ ] **Step 6: Commit**

```bash
git add core/rbac/policy.py core/rbac/context.py tests/test_scope_policy.py
git commit -m "refactor(rbac)!: replace four roles with two access levels"
```

---

### Task 4: Self predicate in sql_guard

**Files:**
- Modify: `core/rbac/sql_guard.py:212-234` (docstring), `:385-452` (the predicate helpers)
- Test: `tests/test_sql_guard_self_scope.py`

**Interfaces:**
- Consumes: `RBACContext` with `is_unrestricted` and `person_id` (Task 3).
- Produces: no new public names. `rewrite_sql(sql: str, rbac_ctx) -> str` keeps its signature.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sql_guard_self_scope.py`:

```python
"""Self-scope predicate injection — the enforcement boundary."""

import pytest

from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext
from core.rbac.sql_guard import rewrite_sql

SELF = RBACContext(access_level=AccessLevel.SELF, person_id=42)
OPEN = RBACContext.unrestricted()


def test_person_table_scoped_to_own_id():
    out = rewrite_sql("SELECT full_name FROM person", SELF)
    assert "person.id = 42" in out.replace('"', "")


def test_person_alias_respected():
    out = rewrite_sql("SELECT p.full_name FROM person p", SELF)
    assert "p.id = 42" in out.replace('"', "")


def test_person_fk_table_scoped():
    out = rewrite_sql("SELECT start_date FROM leave_record", SELF)
    assert "leave_record.person_id = 42" in out.replace('"', "")


def test_person_team_fk_table_scoped():
    out = rewrite_sql("SELECT hours FROM person_week_project", SELF)
    normalised = out.replace('"', "")
    assert "person_team_id IN (SELECT id FROM person_team WHERE person_id = 42)" in normalised


def test_person_free_table_not_scoped():
    out = rewrite_sql("SELECT name FROM department", SELF)
    assert "WHERE" not in out.upper()


def test_unclassified_table_fails_closed():
    with pytest.raises(ValueError, match="not classified"):
        rewrite_sql("SELECT id FROM auth_user", SELF)


def test_or_injection_cannot_escape_scope():
    """The paren-wrapping in _inject_and is what makes this hold."""
    out = rewrite_sql("SELECT full_name FROM person WHERE id = 1 OR 1 = 1", SELF)
    normalised = out.replace('"', "")
    assert "(id = 1 OR 1 = 1) AND person.id = 42" in normalised


def test_missing_person_id_denies_all():
    ctx = RBACContext(access_level=AccessLevel.SELF, person_id=None)
    out = rewrite_sql("SELECT full_name FROM person", ctx)
    assert "1 = 0" in out


def test_unrestricted_gets_no_injection():
    out = rewrite_sql("SELECT full_name FROM person", OPEN)
    assert "WHERE" not in out.upper()


def test_join_scopes_both_tables():
    out = rewrite_sql(
        "SELECT p.full_name, l.start_date FROM person p JOIN leave_record l ON l.person_id = p.id",
        SELF,
    )
    normalised = out.replace('"', "")
    assert "p.id = 42" in normalised
    assert "l.person_id = 42" in normalised


def test_forbidden_column_still_blocked_for_unrestricted():
    with pytest.raises(ValueError, match="Forbidden column"):
        rewrite_sql("SELECT salary FROM person", OPEN)


def test_dml_still_blocked_for_unrestricted():
    with pytest.raises(ValueError, match="Non-SELECT"):
        rewrite_sql("DELETE FROM person", OPEN)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sql_guard_self_scope.py -v`
Expected: FAIL — `AttributeError: 'RBACContext' object has no attribute 'role'` raised from `_scope_sql`

- [ ] **Step 3: Replace the three predicate helpers**

In `core/rbac/sql_guard.py`, delete `_scope_sql`, `_scoped_person_ids_sql`, `_fk_scope_sql`, and `_person_team_fk_scope_sql` (lines 385-452, everything between `_person_alias` and `_inject_and`) and replace with:

```python
def _self_person_id(rbac_ctx: RBACContext) -> int | None:
    """The single person id a restricted requester may see, or None when the
    context is misconfigured (caller must then deny all)."""
    person_id = rbac_ctx.person_id
    return int(person_id) if person_id is not None else None


def _scope_sql(rbac_ctx: RBACContext, person_alias: str) -> str:
    person_id = _self_person_id(rbac_ctx)
    if person_id is None:
        return "1 = 0"
    return f"{person_alias}.id = {person_id}"


def _fk_scope_sql(rbac_ctx: RBACContext, alias: str, fk_column: str) -> str:
    person_id = _self_person_id(rbac_ctx)
    if person_id is None:
        return "1 = 0"
    return f"{alias}.{fk_column} = {person_id}"


def _person_team_fk_scope_sql(rbac_ctx: RBACContext, alias: str) -> str:
    person_id = _self_person_id(rbac_ctx)
    if person_id is None:
        return "1 = 0"
    return (
        f"{alias}.person_team_id IN "
        f"(SELECT id FROM person_team WHERE person_id = {person_id})"
    )
```

- [ ] **Step 4: Fix the one call site that assumed `_scope_sql` could return None**

In `_inject_scope_into_tree` (around line 340), the person branch currently reads:

```python
        alias = _person_alias(select)
        if alias is not None:
            scope_sql = _scope_sql(rbac_ctx, alias)
            if scope_sql:
                _inject_and(select, scope_sql)
```

`_scope_sql` now always returns a non-empty string, so simplify to:

```python
        alias = _person_alias(select)
        if alias is not None:
            _inject_and(select, _scope_sql(rbac_ctx, alias))
```

- [ ] **Step 5: Update the module docstring**

Replace the `rewrite_sql` docstring paragraph that reads `Scope injection is only applied for restricted roles (dept_head, team_lead).` with:

```
    Scope injection is only applied for AccessLevel.SELF requesters; UNRESTRICTED
    (HR / Management group members) get no row predicates.
```

And in the module header, replace the example block:

```
      WHERE department_id = 3 OR 1=1
    the rewrite produces
      WHERE (department_id = 3 OR 1=1) AND department_id = 3
    which correctly restricts the result set to the user's department.
```

with:

```
      WHERE id = 1 OR 1=1
    the rewrite produces
      WHERE (id = 1 OR 1=1) AND person.id = 42
    which correctly restricts the result set to the requester's own row.
```

- [ ] **Step 6: Run the new test file**

Run: `uv run pytest tests/test_sql_guard_self_scope.py -v`
Expected: PASS, 12 passed

- [ ] **Step 7: Run the dangerous-function suite for regressions**

Run: `uv run pytest tests/test_sql_guard_dangerous_functions.py -v`
Expected: FAIL on `RBACContext.superuser()` (line 18) — that is Task 8's cleanup. Confirm the *function-blocking* assertions themselves are unchanged in intent, then move on.

- [ ] **Step 8: Commit**

```bash
git add core/rbac/sql_guard.py tests/test_sql_guard_self_scope.py
git commit -m "feat(rbac): inject self-scope predicates in sql_guard"
```

---

### Task 5: Demote the scope prompt to an advisory hint

**Files:**
- Delete: `core/rbac/prompt.py`
- Modify: `core/agent/prompts.py:95-128`, `core/agent/enrichment.py:24-26`
- Test: `tests/test_agent_prefix_hint.py`

**Interfaces:**
- Consumes: `RBACContext.scope_hint()` (Task 3).
- Produces: `build_prefix(rbac_ctx) -> str` unchanged in signature; `build_enriched_input(user_input, rbac_ctx, schema_block, conversation_history) -> str` unchanged in signature.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_prefix_hint.py`:

```python
"""The prompt carries an advisory hint only — enforcement is in sql_guard."""

from core.agent.enrichment import build_enriched_input
from core.agent.prompts import build_prefix
from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext

SELF = RBACContext(access_level=AccessLevel.SELF, person_id=42)
OPEN = RBACContext.unrestricted()


def test_prefix_for_self_mentions_own_records():
    assert "own records" in build_prefix(SELF).lower()


def test_prefix_for_unrestricted_mentions_company_wide():
    assert "company-wide" in build_prefix(OPEN).lower()


def test_prefix_for_none_context_builds():
    assert build_prefix(None)


def test_prefix_never_leaks_person_id():
    """The hint is advisory; it must not hand the model a scope value to
    'helpfully' filter on, which would mask guard failures in testing."""
    assert "42" not in build_prefix(SELF)


def test_forbidden_columns_still_in_prefix():
    assert "salary" in build_prefix(SELF).lower()


def test_enriched_input_includes_hint():
    out = build_enriched_input("how many people?", SELF, "", None)
    assert "own records" in out.lower()


def test_enriched_input_without_context():
    out = build_enriched_input("how many people?", None, "", None)
    assert "how many people?" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_prefix_hint.py -v`
Expected: FAIL — `AttributeError: 'RBACContext' object has no attribute 'scope_prompt'`

- [ ] **Step 3: Rewrite the prefix builder**

In `core/agent/prompts.py`, replace `_UNRESTRICTED_RBAC`, `_RESTRICTED_RBAC`, and `build_prefix` (lines 95-128) with:

```python
_UNRESTRICTED_RBAC = """Current user access: UNRESTRICTED (full company-wide access).
All employees, departments, and teams are visible."""

_SELF_RBAC = """Current user access: SELF.
{hint}
Results are automatically restricted to this user's own records before any
query runs — do not attempt to widen, guess, or work around that restriction,
and do not ask the user for an employee id to filter by."""


def build_prefix(rbac_ctx) -> str:
    """
    Assemble the full system prefix for a given RBAC context.

    The scope text here is ADVISORY. It exists so the model produces sensible
    answers and explanations for a SELF user, not to enforce anything —
    enforcement is core.rbac.sql_guard.rewrite_sql at the db.run() call site,
    which prompt injection cannot reach. Never put a person id or any other
    scope value in this string.
    """
    if rbac_ctx is None or rbac_ctx.is_unrestricted:
        rbac_prefix = _UNRESTRICTED_RBAC
    else:
        rbac_prefix = _SELF_RBAC.format(hint=rbac_ctx.scope_hint())

    return _BASE_PREFIX.format(
        forbidden_columns=_forbidden_columns_str(),
        rbac_prefix=rbac_prefix,
    )
```

- [ ] **Step 4: Update the enrichment call site**

In `core/agent/enrichment.py`, change line 26 from:

```python
        parts.append(f"[Access control rules for this request]\n{rbac_ctx.scope_prompt()}")
```

to:

```python
        parts.append(f"[Access scope for this request]\n{rbac_ctx.scope_hint()}")
```

- [ ] **Step 5: Delete the old prompt builder**

```bash
git rm core/rbac/prompt.py
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_agent_prefix_hint.py -v`
Expected: PASS, 7 passed

- [ ] **Step 7: Commit**

```bash
git add core/agent/prompts.py core/agent/enrichment.py tests/test_agent_prefix_hint.py
git commit -m "refactor(rbac): demote scope prompt to advisory hint"
```

---

### Task 6: Wire the HTTP and Slack entry points

**Files:**
- Create: `core/rbac/resolution.py`
- Modify: `api/services/query_service.py:27-53`, `adapters/slack.py:286-313`
- Modify: `core/agent/factory.py:19-27` (add the boot assertion)
- Test: `tests/test_rbac_resolution.py`

**Interfaces:**
- Consumes: `ErpIdentityResolver`, `assert_rbac_groups_exist` (Task 2); `resolve_access_level` (Task 1); `RBACContext.for_identity` (Task 3).
- Produces: `resolve_context(person_id: int) -> RBACContext | None`; `get_resolver() -> ErpIdentityResolver` (process-wide, lru_cached).

- [ ] **Step 1: Write the failing test**

Create `tests/test_rbac_resolution.py`:

```python
"""Composition root — person_id to RBACContext."""

from core.rbac.access import AccessLevel
from core.rbac.erp_identity import ErpIdentity
from core.rbac.resolution import resolve_context


class _StubResolver:
    def __init__(self, identity):
        self._identity = identity

    def by_person_id(self, person_id):
        return self._identity


def test_hr_group_member_is_unrestricted():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset({12}))
    ctx = resolve_context(42, resolver=_StubResolver(identity))
    assert ctx.is_unrestricted is True
    assert ctx.person_id == 42


def test_management_group_member_is_unrestricted():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset({13}))
    ctx = resolve_context(42, resolver=_StubResolver(identity))
    assert ctx.is_unrestricted is True


def test_non_member_is_self_scoped():
    identity = ErpIdentity(person_id=42, auth_user_id=500, group_ids=frozenset({9, 10}))
    ctx = resolve_context(42, resolver=_StubResolver(identity))
    assert ctx.is_unrestricted is False
    assert ctx.person_id == 42


def test_unknown_person_returns_none():
    assert resolve_context(42, resolver=_StubResolver(None)) is None


def test_access_level_is_the_configured_pair(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "HR_GROUP_ID", 77, raising=False)
    monkeypatch.setattr(settings, "MANAGEMENT_GROUP_ID", 78, raising=False)
    identity = ErpIdentity(person_id=1, auth_user_id=2, group_ids=frozenset({12}))
    ctx = resolve_context(1, resolver=_StubResolver(identity))
    assert ctx.access_level is AccessLevel.SELF
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rbac_resolution.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.rbac.resolution'`

- [ ] **Step 3: Write the composition root**

Create `core/rbac/resolution.py`:

```python
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


def resolve_context(person_id: int, resolver: ErpIdentityResolver | None = None) -> RBACContext | None:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_rbac_resolution.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Rewire `query_service.resolve_scope`**

In `api/services/query_service.py`, replace the import of `RBACContext` and the body of `resolve_scope` (lines 27-53) with:

```python
def resolve_scope(body: QueryRequest, repo: UserRepo | None = None) -> RBACContext | None:
    """Validate the request and resolve its RBAC scope. Raises HTTPException
    (400/403/503) for the fast-fail cases — always called before any streaming
    starts, so these still come back as ordinary HTTP error responses."""
    repository = repo if repo is not None else HRUserRepository

    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    if not body.slack_user_id:
        return None

    # DB work (user lookup) is scoped to its own short session — deliberately
    # NOT held open across the agent call, which can take up to ~15s.
    with db_session() as session:
        hr_user = repository.get_by_slack_user_id(session, body.slack_user_id)

    if not hr_user:
        raise HTTPException(
            status_code=403,
            detail="User not registered. Ask your HR admin to add your Slack account.",
        )

    # employee_id IS person.id — see core/rbac/models.py.
    try:
        ctx = resolve_context(hr_user.employee_id)
    except SQLAlchemyError as exc:
        # Fail closed: an unreachable ERP denies rather than serving a wider
        # or stale scope.
        logger.warning("rbac_resolution_failed", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="Authorization service unavailable. Please retry.",
        ) from exc

    if ctx is None:
        raise HTTPException(
            status_code=403,
            detail="Your ERP account is inactive or not provisioned.",
        )
    return ctx
```

Add these imports at the top of the file, alongside the existing ones:

```python
import structlog
from sqlalchemy.exc import SQLAlchemyError

from core.rbac.resolution import resolve_context
```

and after the imports:

```python
logger = structlog.get_logger(__name__)
```

Remove the now-unused `from core.rbac.context import RBACContext` only if ruff flags it — it is still needed for the return type annotation, so keep it.

- [ ] **Step 6: Rewire the Slack handler**

In `adapters/slack.py`, replace the `if not hr_user.role:` block (lines 301-311) with:

```python
    # employee_id IS person.id — see core/rbac/models.py.
    try:
        rbac_ctx = resolve_context(hr_user.employee_id)
    except SQLAlchemyError as exc:
        logger.warning("slack_rbac_resolution_failed", slack_user_id=slack_user_id, error=str(exc))
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="I can't verify your access right now. Please try again in a minute.",
            )
        except Exception:
            pass
        return

    if rbac_ctx is None:
        logger.warning("slack_user_not_provisioned", slack_user_id=slack_user_id)
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="Your ERP account is inactive or not provisioned. Please contact HR.",
            )
        except Exception:
            pass
        return
```

Then delete the now-redundant line further down:

```python
    rbac_ctx = RBACContext.for_user(hr_user)
```

Update the imports at the top of `adapters/slack.py`: remove `from core.rbac.context import RBACContext`, add:

```python
from sqlalchemy.exc import SQLAlchemyError

from core.rbac.resolution import resolve_context
```

Also update the module docstring line 9 from `Look up the HRUser for the Slack user ID and build an RBACContext.` to `Look up the HRUser for the Slack user ID, resolve its ERP access level, and build an RBACContext.`

- [ ] **Step 7: Add the boot assertion**

In `core/agent/factory.py`, extend `_get_included_tables` (lines 19-27) — rename it to make the widened responsibility honest:

```python
def _get_included_tables() -> list[str]:
    tables = settings.included_tables
    if not tables:
        raise ValueError(
            "INCLUDED_TABLES must be set in .env. "
            "List only the tables the agent needs (e.g. person,department,leave_record)."
        )
    assert_tables_classified(tables)
    # Fail the boot, not the request, if the configured RBAC groups have been
    # renamed or deleted in the ERP — a rename must not silently change who
    # has company-wide access.
    assert_rbac_groups_exist(
        erp_engine(),
        {
            settings.HR_GROUP_ID: settings.HR_GROUP_NAME,
            settings.MANAGEMENT_GROUP_ID: settings.MANAGEMENT_GROUP_NAME,
        },
    )
    return tables
```

Add to the imports in `core/agent/factory.py`:

```python
from core.db import erp_engine
from core.rbac.erp_identity import assert_rbac_groups_exist
```

- [ ] **Step 8: Run the affected suites**

Run: `uv run pytest tests/test_rbac_resolution.py tests/test_query_service_injection.py tests/test_slack_adapter.py -v`
Expected: `test_rbac_resolution.py` passes. `test_query_service_injection.py` fails on `RBACContext.superuser()` (line 25) — Task 8. Note any `test_slack_adapter.py` failures that are about `hr_user.role` and fix them there in Task 8.

- [ ] **Step 9: Commit**

```bash
git add core/rbac/resolution.py api/services/query_service.py adapters/slack.py core/agent/factory.py tests/test_rbac_resolution.py
git commit -m "feat(rbac): resolve access level from ERP at both entry points"
```

---

### Task 7: Drop the role columns

**Files:**
- Modify: `core/rbac/models.py:63-70`
- Modify: `api/schemas/users.py`, `api/services/user_service.py:21-28`, `api/admin.py:12,20-50`
- Create: `migrations/versions/0005_drop_hruser_role_scope.py`
- Test: `tests/test_user_service_no_role.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `HRUser` with fields `id`, `employee_id`, `slack_user_id`, `is_active`, `created_at`, `updated_at` only; `UserCreate(employee_id: int, slack_user_id: str)`; `UserResponse(id, employee_id, slack_user_id, is_active)`.

- [ ] **Step 1: Confirm the current migration head**

Run: `uv run alembic heads`
Expected: a single head. Record its revision id — the new migration's `down_revision` must be exactly that string.

- [ ] **Step 2: Write the failing test**

Create `tests/test_user_service_no_role.py`:

```python
"""HRUser is a Slack-ID mapping only — no role or scope columns."""

import pytest
import sqlalchemy
from sqlalchemy.orm import Session

from api.schemas.users import UserCreate
from api.services.user_service import register_user
from core.rbac.models import Base, HRUser


@pytest.fixture
def session():
    engine = sqlalchemy.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_hruser_has_no_role_column():
    assert not hasattr(HRUser, "role")
    assert not hasattr(HRUser, "department_id")
    assert not hasattr(HRUser, "team_id")


def test_register_user_without_role(session):
    created = register_user(session, UserCreate(employee_id=42, slack_user_id="U123"))
    assert created.employee_id == 42
    assert created.slack_user_id == "U123"


def test_user_create_rejects_role_field():
    with pytest.raises(Exception):
        UserCreate(employee_id=42, slack_user_id="U123", role="hr_manager")
```

Note: `UserCreate` must set `model_config = ConfigDict(extra="forbid")` for the last test to pass — that is deliberate, so a stale client sending `role` gets a 422 instead of silently having it ignored.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_user_service_no_role.py -v`
Expected: FAIL — `HRUser` still has `role`

- [ ] **Step 4: Trim the model**

In `core/rbac/models.py`, delete these lines from `HRUser` (lines 63-70):

```python
    role = Column(String(20), nullable=False)

    slack_user_id = Column(String(20), nullable=True)

    # Scope columns — only relevant for DEPT_HEAD and TEAM_LEAD.
    # CTO/CEO and HR_MANAGER leave these NULL (full access).
    department_id = Column(Integer, nullable=True)
    team_id = Column(Integer, nullable=True)
```

and replace with:

```python
    slack_user_id = Column(String(20), nullable=True)
```

Update `HRUser.__repr__` (lines 87-90) to:

```python
    def __repr__(self) -> str:
        return f"<HRUser id={self.id} employee_id={self.employee_id} slack={self.slack_user_id}>"
```

Update the class docstring region comment at line 59-61 to note that `employee_id` is `person.id` and is the RBAC anchor:

```python
    # Link to the ERP person row — not a FK so the table works even if
    # the ERP schema changes or lives on a different logical DB. This IS
    # person.id, and it is the sole input to RBAC resolution
    # (core/rbac/resolution.py).
    employee_id = Column(Integer, nullable=False, index=True)
```

- [ ] **Step 5: Trim the schemas**

Replace `api/schemas/users.py` entirely with:

```python
from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    # extra="forbid" so a stale client still sending `role`/`department_id`
    # gets a 422 rather than having the field silently ignored.
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    slack_user_id: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    slack_user_id: str | None
    is_active: bool
```

- [ ] **Step 6: Trim the service**

In `api/services/user_service.py`, replace the `HRUser(...)` construction (lines 22-28) with:

```python
    user = HRUser(
        employee_id=body.employee_id,
        slack_user_id=body.slack_user_id,
    )
```

- [ ] **Step 7: Trim the admin view**

In `api/admin.py`, delete the `from core.rbac.roles import Role` import (line 12) and the `_ROLE_CHOICES` assignment (line 20). Replace the `HRUserAdmin` column/form lists with:

```python
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
```

Delete the `form_overrides = {"role": SelectField}` line and, if `SelectField` is now unused, its import.

- [ ] **Step 8: Write the migration**

Create `migrations/versions/0005_drop_hruser_role_scope.py`, substituting the revision id recorded in Step 1 for `<HEAD_FROM_STEP_1>`:

```python
"""Drop role/department_id/team_id from hr_assistant_users.

RBAC is now resolved from ERP group membership at request time
(core/rbac/resolution.py); these columns are no longer read by anything.

IRREVERSIBLE IN PRACTICE: downgrade() recreates the columns but cannot
restore their values. Snapshot hr_assistant_users before upgrading.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_drop_hruser_role_scope"
down_revision: str | Sequence[str] | None = "<HEAD_FROM_STEP_1>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("hr_assistant_users") as batch:
        batch.drop_column("role")
        batch.drop_column("department_id")
        batch.drop_column("team_id")


def downgrade() -> None:
    # Columns come back empty — the role/scope values are not recoverable.
    with op.batch_alter_table("hr_assistant_users") as batch:
        batch.add_column(sa.Column("role", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("department_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
```

- [ ] **Step 9: Check migration naming and run the migration**

Run: `bash scripts/check_migration_naming.sh`
Expected: PASS

Run: `uv run alembic upgrade head`
Expected: completes without error

**Before running this against any shared database, snapshot the table:**

```bash
pg_dump --table=hr_assistant_users "$APP_DATABASE_URL" > /tmp/hr_assistant_users_backup.sql
```

- [ ] **Step 10: Run the tests**

Run: `uv run pytest tests/test_user_service_no_role.py -v`
Expected: PASS, 3 passed

- [ ] **Step 11: Commit**

```bash
git add core/rbac/models.py api/schemas/users.py api/services/user_service.py api/admin.py migrations/versions/0005_drop_hruser_role_scope.py tests/test_user_service_no_role.py
git commit -m "refactor(rbac)!: drop role and scope columns from hr_assistant_users"
```

---

### Task 8: Delete the role model and prune the old suites

**Files:**
- Delete: `core/rbac/roles.py`
- Modify: `tests/test_rbac.py`, `tests/test_scope_execution.py`, `tests/test_sql_guard_dangerous_functions.py:18`, `tests/test_query_service_injection.py:25`, `tests/test_agent_scoped_run.py`
- Modify: `AGENTS.md`, `SPEC.md`

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: a green suite with no references to `Role`, `scope_prompt`, `for_user`, `superuser`, `department_id`, or `team_id`.

- [ ] **Step 1: Find every remaining reference**

Run:

```bash
grep -rn "core.rbac.roles\|Role\.\|scope_prompt\|for_user\|\.superuser()\|can_see_employee" api core adapters tests scripts
```

Expected: hits only in `tests/` and the docs listed above. Any hit in `api/`, `core/`, or `adapters/` means an earlier task was left incomplete — go back and finish it before continuing.

- [ ] **Step 2: Delete the roles module**

```bash
git rm core/rbac/roles.py
```

- [ ] **Step 3: Replace the old RBAC suite**

`tests/test_rbac.py` is 105 tests, of which roughly 60 assert on `Role`, `scope_prompt()` text, or dept/team injection. Those behaviours no longer exist.

Delete these classes/sections wholesale (they are superseded by `tests/test_scope_policy.py`, `tests/test_sql_guard_self_scope.py`, and `tests/test_agent_prefix_hint.py`):

- every test asserting on `scope_prompt` (lines ~39-160)
- the `can_see_employee` section (lines ~201-230)
- the `for_user` construction tests (lines ~247-262)
- the dept_head / team_lead injection tests (lines ~330 onward)

Keep and do not modify:

- the `FORBIDDEN_COLUMNS` membership tests (lines ~156-159)
- the `ForbiddenColumnRedactor` tests (lines ~173-194)

Rename the file to `tests/test_redaction.py`, since redaction is all that is left in it:

```bash
git mv tests/test_rbac.py tests/test_redaction.py
```

- [ ] **Step 4: Fix the two `superuser()` call sites**

In `tests/test_query_service_injection.py` line 25 and `tests/test_sql_guard_dangerous_functions.py` line 18, change:

```python
RBACContext.superuser()
```

to:

```python
RBACContext.unrestricted()
```

- [ ] **Step 5: Update the scope-execution suite**

`tests/test_scope_execution.py` is the highest-value suite in the repo — it seeds a real DB (SQLite by default, Postgres when `TEST_DATABASE_URL` is set), executes the rewritten SQL, and asserts on **rows**, which is what catches anchor bugs like the CROSS JOIN bypass. Preserve that structure; only the contexts change.

Its existing seed already provides four people:

```
101 Alice  dept 3, team 7 (+ an inactive team-8 row)
102 Bob    dept 3, team 8
103 Carol  dept 4, team 7
104 Dave   dept 4, team 8
```

Change the imports — delete `from core.rbac.roles import Role`, add:

```python
from core.rbac.access import AccessLevel
```

Replace the `_ctx` helper with:

```python
def _ctx(person_id):
    """Self-scoped context for the given person."""
    return RBACContext(access_level=AccessLevel.SELF, person_id=person_id)
```

Add these module constants next to the existing `DEPT3_PERSONS` block:

```python
ALICE = 101
ALICE_ONLY = {101}
```

Delete the `DeptHead` and `TeamLead` test classes entirely (lines ~186-255) — dept and team scope no longer exist — and the two misconfiguration tests at lines ~278-283. Replace them with one class:

```python
class TestSelfScope:
    def test_direct_person_select(self, conn):
        rows = _run(conn, "SELECT id, department_id FROM person", _ctx(ALICE))
        assert {r[0] for r in rows} == ALICE_ONLY

    def test_cross_join_person_does_not_leak_fk_table(self, conn):
        """The CROSS JOIN bypass: person's predicate must not be relied on to
        constrain leave_record."""
        rows = _run(
            conn,
            "SELECT l.person_id FROM leave_record l CROSS JOIN person p",
            _ctx(ALICE),
        )
        assert {r[0] for r in rows} == ALICE_ONLY

    def test_fk_table_alone_is_scoped(self, conn):
        rows = _run(conn, "SELECT person_id FROM leave_record", _ctx(ALICE))
        assert {r[0] for r in rows} == ALICE_ONLY

    def test_person_team_fk_table_is_scoped(self, conn):
        rows = _run(conn, "SELECT person_id FROM person_week_log", _ctx(ALICE))
        assert {r[0] for r in rows} == ALICE_ONLY

    def test_or_injection_cannot_widen(self, conn):
        rows = _run(
            conn,
            "SELECT id FROM person WHERE id = 101 OR 1 = 1",
            _ctx(ALICE),
        )
        assert {r[0] for r in rows} == ALICE_ONLY

    def test_subquery_on_person_is_scoped(self, conn):
        rows = _run(
            conn,
            "SELECT id FROM person WHERE id IN (SELECT id FROM person)",
            _ctx(ALICE),
        )
        assert {r[0] for r in rows} == ALICE_ONLY

    def test_missing_person_id_returns_nothing(self, conn):
        """A SELF context with no person_id is misconfigured — deny, don't widen."""
        broken = RBACContext(access_level=AccessLevel.SELF, person_id=None)
        rows = _run(conn, "SELECT id FROM person", broken)
        assert rows == []
```

In the unrestricted class (lines ~262-267), replace the `@pytest.mark.parametrize("role", [Role.CTO_CEO, Role.HR_MANAGER])` decorator and its test with:

```python
    def test_unrestricted_sees_all_people(self, conn):
        rows = _run(conn, "SELECT id FROM person", RBACContext.unrestricted())
        assert {r[0] for r in rows} == ALL_PERSONS
```

Leave `test_none_ctx_sees_all` unchanged.

Finally update the module docstring's seed-layout block: the dept/team divergence commentary is still accurate for the seed data, but add a line noting that self-scope tests use Alice (101) and that the divergence now only guards against the guard anchoring on the wrong *table*, not the wrong dimension.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS, no failures, no errors

If anything still fails, fix it here — this task is not complete with a red suite.

- [ ] **Step 7: Update the docs**

In `SPEC.md`, replace the FR-5.2 four-role table and the FR-5.3 – FR-5.7 scope rules with the two access levels, and add a line pointing at `docs/superpowers/specs/2026-07-27-deterministic-rbac-design.md`.

In `AGENTS.md`, update the RBAC section to describe: identity resolved from ERP group membership at request time, two access levels, `sql_guard` as the sole enforcement point, and the prompt hint as advisory only.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(rbac)!: remove Role model and update docs"
```

---

### Task 9: Pre-cutover verification against production

**Files:** none — this is an operational gate, run before the change is deployed.

**Interfaces:**
- Consumes: the deployed config from Task 1.
- Produces: a go/no-go decision.

- [ ] **Step 1: Confirm the group IDs resolve to the expected names**

Against the production ERP (read-only):

```sql
SELECT id, name FROM auth_group WHERE id IN (12, 13);
```

Expected: `12 | Pod` and `13 | Management`. Anything else — stop and update the config; do not deploy.

- [ ] **Step 2: Sanity-check membership plausibility**

```sql
SELECT d.name AS dept, count(*) AS members
FROM auth_user_groups ug
JOIN auth_group g ON g.id = ug.group_id
JOIN person p ON p.user_id = ug.user_id
LEFT JOIN department d ON d.id = p.department_id
WHERE g.id = 12
GROUP BY 1 ORDER BY 2 DESC;
```

Expected: dominated by the HR/People-Ops department. In the dev copy this returned 30 Engineering / 15 QA / 3 People Ops — if production looks like that too, **stop**. It means the group choice is wrong, not the design, and the spec needs revisiting before cutover.

- [ ] **Step 3: Count who gains and loses access**

```sql
SELECT count(DISTINCT ug.user_id)
FROM auth_user_groups ug WHERE ug.group_id IN (12, 13);
```

Compare against the current `hr_assistant_users` row count. Confirm with the product owner that the delta is intended before deploying.

---

## Post-implementation notes

Carry these into the follow-up backlog; they are deliberately **not** in scope here (see the spec's *Out of scope*):

- **SSO login.** This plan resolves identity from `hr_assistant_users.employee_id`. The spec's email → `auth_user` path needs an SSO flow that does not exist yet. That work adds `ErpIdentityResolver.by_email` and a session layer, and should get its own spec.
- **New exposure to accept knowingly.** `SELF` users can read all 12 person-free lookup tables company-wide — every department, team, leave type, and holiday — plus `team.lead_id` via the documented exception in `_APPROVED_PERSON_FK_EXCEPTIONS`. Under the old design nobody below `dept_head` reached the bot at all.
- **ERP is now a hard dependency** of authorization. An ERP outage returns 503 instead of degrading. Monitor accordingly.
