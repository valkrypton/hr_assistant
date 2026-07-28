# Google Workspace SSO Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an end user log into HR Assistant with their `arbisoft.com` Google Workspace account, get identified against the ERP by email, and call `POST /query` using a session cookie — with no admin vouching and no Slack registration required for them.

**Architecture:** `GET /auth/login` redirects to Google (Authlib), `GET /auth/callback` verifies the ID token's `hd` claim and `email_verified`, looks up the email in the ERP via a new `ErpIdentityResolver.by_email`, and — if found — sets a stateless `itsdangerous`-signed cookie carrying `person_id`. `POST /query` checks that cookie first and falls back to today's unchanged admin/Slack path if it's absent. No new database table, no server-side session store — revocation freshness comes from the RBAC resolver's existing 15-minute TTL re-check of `person.is_active`.

**Tech Stack:** FastAPI, Authlib (`authlib.integrations.starlette_client`), `itsdangerous` (already a dependency), Starlette `SessionMiddleware` (needed for Authlib's OAuth state, not currently on the main app), pydantic-settings, pytest, `uv` for all commands.

## Global Constraints

- All commands run through `uv` (`uv run pytest`, `uv add <package>`). Never `pip` or a bare `python`.
- `pre-commit` runs ruff lint + format on commit. Never bypass with `--no-verify`.
- Login is restricted to Google Workspace domain `arbisoft.com`, enforced server-side via the ID token's `hd` claim — not just as a login-screen hint.
- Stateless cookie only. No new database table, no JWT bearer token. This was an explicit choice over a DB-backed session store (rejected: the RBAC resolver's existing `RBAC_CACHE_TTL_SECONDS`-bounded re-check of `person.is_active` was judged sufficient revocation freshness without new infra).
- `core/` vs `api/` vs `adapters/` layering is unchanged. `adapters/slack.py` still cannot import `api/`. All new auth code lives in `api/` (explicit user correction — auth and its config are an API-layer concern).
- Auth-specific config (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_WORKSPACE_DOMAIN`, `SESSION_COOKIE_MAX_AGE_SECONDS`, `POST_LOGIN_REDIRECT_URL`) lives in a **new, separate** `api/config.py` `BaseSettings` class — explicitly not added as fields on the existing `core.config.Settings`.
- Session cookie sign/verify logic lives in the **existing** `api/auth.py` — not a new `core/auth/` package.
- The existing Slack integration and `HRUser`/`hr_assistant_users` code (`adapters/slack.py`, `core/rbac/models.py`, `api/routes/users.py`, `api/services/user_service.py`, `api/schemas/users.py`, `api/admin.py`) is kept as-is, not deleted, not behaviorally changed — only comment-marked deprecated.
- Per `CLAUDE.md`: this is security-relevant work, but test *execution* and test *writing* are still delegated to a cheaper model rather than iterated on directly in a Fable session — every step below is written to be executable by such a model without further judgment calls.

---

## Prerequisite context — read before Task 1

This repo currently has a large set of **uncommitted** working-tree changes implementing deterministic RBAC (a prior branch's commits were soft-reset to `main` earlier this session, deliberately keeping the file diffs uncommitted). This plan builds directly on top of that code — do not redesign or reimplement any of it:

- `core/rbac/access.py` — `AccessLevel` enum, `resolve_access_level(group_ids, hr_group_id, management_group_id)`.
- `core/rbac/erp_identity.py` — `ErpIdentityResolver` with `by_person_id(person_id) -> ErpIdentity | None`, TTL-cached, fail-closed. `ErpIdentity` is `@dataclass(frozen=True)` with `person_id`, `auth_user_id`, `group_ids`.
- `core/rbac/resolution.py` — `resolve_context(person_id, resolver=None) -> RBACContext | None` and `get_resolver() -> ErpIdentityResolver` (process-wide, `lru_cache(maxsize=1)`).
- `core/rbac/context.py` / `core/rbac/policy.py` — `RBACContext(access_level, person_id)`, `RBACContext.unrestricted()`, `RBACContext.for_identity(identity, access_level)`, `.scope_hint()`, `.is_unrestricted`.
- `core/rbac/sql_guard.py` — self-scope SQL predicate injection.
- `core/config.py` — `settings.HR_GROUP_ID`, `settings.MANAGEMENT_GROUP_ID`, `settings.RBAC_CACHE_TTL_SECONDS`, `settings.SECRET_KEY` (a `SecretStr`).
- `api/services/query_service.py` — `resolve_scope(body, repo=None, resolve_ctx=None) -> RBACContext | None` already has an injectable `resolve_ctx: ScopeResolver | None` parameter for DI.
- `api/services/interfaces.py` — `ScopeResolver(Protocol)` with `__call__(self, person_id: int) -> RBACContext | None`.

Separately, during this session an unrelated boot-time check (`assert_rbac_groups_exist`, and its call from `api/main.py`'s `lifespan`) was deliberately removed from both `core/rbac/erp_identity.py` and `api/main.py`. Two test files still reference the now-deleted function and currently fail as a result — **Task 1 fixes this** before any new work starts.

`authlib>=1.7.2` has already been added to `pyproject.toml` (verified installed: `authlib==1.7.2`, plus `cryptography`, `joserfc`). Task 8 depends on it; no action needed to add it.

**Verified facts used throughout this plan** (confirmed against the actual files/environment, not assumed):

- ERP's `auth_user` table has an `email` column (`character varying(254) not null`), confirmed via `psql` against the local ERP dev copy.
- `api/deps.py` currently has `require_admin`, `require_admin_unless_open` (both `HTTPBasic`-based), `get_db`, `DbDep`, `OptionalAdminDep = Annotated[AdminUser | None, Depends(require_admin_unless_open)]`. It re-exports `app_engine`/`db_session`/`erp_engine` from `core/db.py`.
- `api/auth.py` currently has exactly `hash_password`/`verify_password` (bcrypt, for the admin panel). Nothing else.
- `api/routes/query.py`'s `run_query(body: QueryRequest, admin: OptionalAdminDep)` calls `resolve_scope(body)` via `run_in_threadpool`, ignoring `admin`'s value (it's just a gate).
- `api/schemas/query.py`: `QueryRequest(query: str, slack_user_id: str | None = None)`, `QueryResponse(answer: str)`. `slack_user_id` stays — it's the existing back-compat path.
- `pyproject.toml` already depends on `itsdangerous`, currently used only by `api/main.py`'s `AdminAuth` (SQLAdmin's session backend). The new session cookie reuses this same library.
- `sqladmin`'s `AuthenticationBackend.__init__` builds its **own** `SessionMiddleware`, scoped only to the `/admin` sub-app (confirmed by reading the installed package source: `self.middlewares = [Middleware(SessionMiddleware, secret_key=secret_key, **session_kwargs)]`). The main FastAPI `app` has **no** `SessionMiddleware` today. Authlib's Starlette OAuth client stores CSRF `state`/`nonce` in `request.session` during the redirect round-trip (confirmed by reading `authlib.integrations.starlette_client.integration.StarletteIntegration.set_state_data`/`get_state_data`, which fall back to `session[key]` when no external cache is configured) — so Task 9 must add a `SessionMiddleware` to the main app.
- Authlib's `StarletteOAuth2App.authorize_redirect(request, redirect_uri=None, **kwargs)` forwards extra kwargs (e.g. `hd="arbisoft.com"`) into the Google authorize URL. `authorize_access_token(request, **kwargs)` returns a `dict`; when the flow used `openid` scope, it verifies the ID token's signature via the discovery doc's JWKS and attaches parsed claims as `token["userinfo"]` (confirmed by reading the installed Authlib source).
- `index.html` is a static file, not served by FastAPI at all (no `StaticFiles` mount, no `/` route in any `api/routes/*.py`) — confirmed by grep. It has a configurable "API URL" input field, meaning the frontend and backend origin are not assumed to be the same in dev. `postQuery()` currently does a plain `fetch(base + '/query', {method: 'POST', headers, body})` with **no** `credentials: 'include'` — cross-origin cookies would not be sent as written today.
- `api/main.py`'s `CORSMiddleware` currently has no `allow_credentials=True` — required for the browser to send/accept cookies cross-origin, and per the Fetch spec cannot be combined with a wildcard origin (this repo's own production guard already forbids `CORS_ALLOW_ORIGINS=*` when `DEBUG=false`, so this is compatible, just needs the flag added).
- There is no fixed, known frontend URL in this app's current topology (see `index.html` note above). `POST_LOGIN_REDIRECT_URL` is therefore a config value defaulting to empty string; when empty, `/auth/callback` renders a small inline HTML confirmation instead of guessing a redirect target.

---

### Task 1: Fix the stale `assert_rbac_groups_exist` test references

**Files:**
- Modify: `tests/test_erp_identity.py`
- Modify: `tests/test_e2e.py:153` (the `client` fixture)

**Interfaces:**
- Consumes: nothing new.
- Produces: a green baseline (`uv run pytest` passes) for every later task to build on.

- [ ] **Step 1: Remove the stale import and tests from `tests/test_erp_identity.py`**

Open `tests/test_erp_identity.py`. Change the import block from:

```python
from core.rbac.erp_identity import (
    ErpIdentity,
    ErpIdentityResolver,
    assert_rbac_groups_exist,
)

HR = 12
MGMT = 13
```

to:

```python
from core.rbac.erp_identity import ErpIdentity, ErpIdentityResolver
```

Delete these three test functions entirely (they test the now-removed `assert_rbac_groups_exist`):

```python
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

Leave the rest of the file (the `erp_engine` fixture, all `by_person_id`/cache/error tests) untouched.

- [ ] **Step 2: Remove the stale patch from `tests/test_e2e.py`**

Find this block in the `client` fixture (around line 148-155):

```python
        with (
            patch("core.agent.get_agent"),  # skip LLM warmup in lifespan
            # DATABASE_URL points at the test SQLite file above, not a real
            # ERP, so the RBAC-group boot check has nothing to verify here.
            patch("core.rbac.erp_identity.assert_rbac_groups_exist"),
        ):
```

Replace with:

```python
        with patch("core.agent.get_agent"):  # skip LLM warmup in lifespan
```

- [ ] **Step 3: Run the full suite to confirm a green baseline**

Run: `uv run pytest -q`
Expected: `268 passed` (222 that were already passing + the 46 `test_e2e.py` tests this unblocks), no errors, no failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_erp_identity.py tests/test_e2e.py
git commit -m "test: remove stale assert_rbac_groups_exist references"
```

---

### Task 2: `ErpIdentityResolver.by_email`

**Files:**
- Modify: `core/rbac/erp_identity.py`
- Test: `tests/test_erp_identity.py`

**Interfaces:**
- Consumes: `ErpIdentityResolver` (existing), `ErpIdentity` (existing).
- Produces: `ErpIdentityResolver.by_email(email: str) -> ErpIdentity | None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_erp_identity.py` — first, add an `email` column to the fixture's `auth_user` table and seed data. Change the fixture's `CREATE TABLE auth_user` and its `INSERT` to:

```python
        conn.execute(
            sqlalchemy.text(
                "CREATE TABLE auth_user (id INTEGER PRIMARY KEY, is_active INTEGER NOT NULL, "
                "email VARCHAR(254) NOT NULL DEFAULT '')"
            )
        )
```

and:

```python
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO auth_user (id, is_active, email) VALUES "
                "(500, 1, 'alice@arbisoft.com'), (501, 1, 'bob@arbisoft.com'), "
                "(502, 0, 'inactive.user@arbisoft.com')"
            )
        )
```

Then append these tests to the same file:

```python
def test_by_email_resolves_active_person(erp_engine):
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    identity = resolver.by_email("alice@arbisoft.com")
    assert identity == ErpIdentity(person_id=100, auth_user_id=500, group_ids=frozenset({12, 9}))


def test_by_email_is_case_insensitive(erp_engine):
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    identity = resolver.by_email("ALICE@ARBISOFT.COM")
    assert identity is not None
    assert identity.person_id == 100


def test_by_email_unknown_returns_none(erp_engine):
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    assert resolver.by_email("nobody@arbisoft.com") is None


def test_by_email_inactive_auth_user_returns_none(erp_engine):
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900)
    assert resolver.by_email("inactive.user@arbisoft.com") is None


def test_by_email_shares_cache_with_by_person_id(erp_engine):
    """Login (by_email) and every later /query (by_person_id) must hit the
    same TTL cache, not two independent ones."""
    clock = iter([0.0, 10.0])
    resolver = ErpIdentityResolver(erp_engine, ttl_seconds=900, clock=lambda: next(clock))
    resolver.by_email("alice@arbisoft.com")
    with erp_engine.begin() as conn:
        conn.execute(sqlalchemy.text("DELETE FROM auth_user_groups WHERE user_id = 500"))
    # Still within TTL — by_person_id must see the cached identity from by_email.
    assert resolver.by_person_id(100).group_ids == frozenset({12, 9})
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_erp_identity.py -v`
Expected: the 5 new tests FAIL with `AttributeError: 'ErpIdentityResolver' object has no attribute 'by_email'`.

- [ ] **Step 3: Implement `by_email`**

In `core/rbac/erp_identity.py`, add a new query constant near the existing `_IDENTITY_SQL`:

```python
_EMAIL_TO_PERSON_SQL = sqlalchemy.text(
    "SELECT p.id AS person_id "
    "FROM person p "
    "JOIN auth_user u ON u.id = p.user_id "
    "WHERE lower(u.email) = lower(:email) AND p.is_active AND u.is_active"
)
```

Add this method to `ErpIdentityResolver`, right after `by_person_id`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_erp_identity.py -v`
Expected: all tests PASS (12 pre-existing + 5 new = 17 passed).

- [ ] **Step 5: Commit**

```bash
git add core/rbac/erp_identity.py tests/test_erp_identity.py
git commit -m "feat(rbac): add ErpIdentityResolver.by_email for SSO login"
```

---

### Task 3: `api/config.py` — auth-specific settings

**Files:**
- Create: `api/config.py`
- Test: `tests/test_api_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AuthSettings` (pydantic `BaseSettings`) and a module-level singleton `auth_settings: AuthSettings`, with fields `GOOGLE_CLIENT_ID: str`, `GOOGLE_CLIENT_SECRET: SecretStr`, `GOOGLE_WORKSPACE_DOMAIN: str = "arbisoft.com"`, `SESSION_COOKIE_MAX_AGE_SECONDS: int`, `POST_LOGIN_REDIRECT_URL: str = ""`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_config.py`:

```python
"""api/config.py — auth-specific settings, deliberately separate from
core.config.Settings (an explicit architectural choice: auth config lives
in the API layer, not the shared core settings object)."""

from pydantic import SecretStr

from api.config import AuthSettings


def test_defaults():
    settings = AuthSettings(_env_file=None)
    assert settings.GOOGLE_CLIENT_ID == ""
    assert isinstance(settings.GOOGLE_CLIENT_SECRET, SecretStr)
    assert settings.GOOGLE_CLIENT_SECRET.get_secret_value() == ""
    assert settings.GOOGLE_WORKSPACE_DOMAIN == "arbisoft.com"
    assert settings.SESSION_COOKIE_MAX_AGE_SECONDS == 8 * 60 * 60
    assert settings.POST_LOGIN_REDIRECT_URL == ""


def test_client_secret_does_not_leak_via_repr():
    settings = AuthSettings(_env_file=None, GOOGLE_CLIENT_SECRET="super-secret-value")
    assert "super-secret-value" not in repr(settings.GOOGLE_CLIENT_SECRET)
    assert "super-secret-value" not in str(settings.GOOGLE_CLIENT_SECRET)
    assert settings.GOOGLE_CLIENT_SECRET.get_secret_value() == "super-secret-value"


def test_singleton_is_constructed():
    from api.config import auth_settings

    assert isinstance(auth_settings, AuthSettings)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.config'`

- [ ] **Step 3: Write the implementation**

Create `api/config.py`:

```python
"""Auth-specific configuration for the API layer.

Deliberately separate from core.config.Settings: this is API-layer concern
(Google OAuth client + session cookie policy for the HTTP surface), not
shared business-logic config that core/ or adapters/ need. Mirrors
core/config.py's pattern (same env_file, same SecretStr handling for
secrets) so the two stay consistent in style without being the same object.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Google OAuth client — create at https://console.cloud.google.com/apis/credentials
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: SecretStr = SecretStr("")

    # Only Google accounts on this Workspace domain may log in. Enforced
    # server-side against the ID token's `hd` claim in api/routes/auth.py —
    # not just passed to Google as a login-screen hint.
    GOOGLE_WORKSPACE_DOMAIN: str = "arbisoft.com"

    # How long the signed session cookie stays valid. Default 8 hours.
    SESSION_COOKIE_MAX_AGE_SECONDS: int = 8 * 60 * 60

    # Where /auth/callback redirects after a successful login. Empty by
    # default: this app's frontend (index.html) is a static file with no
    # fixed, known origin (it has its own configurable "API URL" field), so
    # guessing a redirect target would be wrong more often than right. When
    # empty, the callback renders a small inline HTML confirmation instead.
    POST_LOGIN_REDIRECT_URL: str = ""


auth_settings = AuthSettings()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_api_config.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Document the new env vars**

In `README.md`'s env-var table (where `RBAC_CACHE_TTL_SECONDS` etc. are documented), add:

```markdown
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth client credentials for SSO login (console.cloud.google.com) |
| `GOOGLE_WORKSPACE_DOMAIN` | Only Google accounts on this domain may log in (default `arbisoft.com`), enforced server-side |
| `SESSION_COOKIE_MAX_AGE_SECONDS` | How long a login session cookie lasts (default 28800 = 8h) |
| `POST_LOGIN_REDIRECT_URL` | Where to send the browser after login; empty shows an inline confirmation page instead |
```

- [ ] **Step 6: Commit**

```bash
git add api/config.py tests/test_api_config.py README.md
git commit -m "feat(auth): add api/config.py for Google SSO settings"
```

---

### Task 4: `api/auth.py` — session cookie sign/verify

**Files:**
- Modify: `api/auth.py`
- Test: `tests/test_api_auth.py`

**Interfaces:**
- Consumes: `core.config.settings.SECRET_KEY` (existing), `api.config.auth_settings.SESSION_COOKIE_MAX_AGE_SECONDS` (Task 3).
- Produces: `SESSION_COOKIE_NAME: str`, `create_session_cookie(person_id: int) -> str`, `read_session_cookie(cookie: str) -> int | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_auth.py`:

```python
"""api/auth.py — password hashing (existing) + session cookie sign/verify (new)."""

import time

import pytest

from api.auth import (
    SESSION_COOKIE_NAME,
    create_session_cookie,
    hash_password,
    read_session_cookie,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_session_cookie_roundtrip():
    cookie = create_session_cookie(42)
    assert read_session_cookie(cookie) == 42


def test_session_cookie_name_is_stable():
    assert SESSION_COOKIE_NAME == "hr_session"


def test_tampered_cookie_returns_none():
    cookie = create_session_cookie(42)
    tampered = cookie[:-1] + ("a" if cookie[-1] != "a" else "b")
    assert read_session_cookie(tampered) is None


def test_garbage_cookie_returns_none():
    assert read_session_cookie("not-a-real-cookie") is None


def test_expired_cookie_returns_none(monkeypatch):
    from api.config import auth_settings

    monkeypatch.setattr(auth_settings, "SESSION_COOKIE_MAX_AGE_SECONDS", 1)
    cookie = create_session_cookie(42)
    time.sleep(1.5)
    assert read_session_cookie(cookie) is None


def test_cookie_for_different_person_ids_differs():
    assert create_session_cookie(1) != create_session_cookie(2)
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_api_auth.py -v`
Expected: `test_password_hash_roundtrip` PASSES (existing behavior); the other 6 FAIL with `ImportError: cannot import name 'SESSION_COOKIE_NAME'`.

- [ ] **Step 3: Implement the session cookie functions**

Replace the full content of `api/auth.py` with:

```python
"""Password hashing for the admin panel, and session-cookie sign/verify for
the Google SSO login flow. Both are HTTP-layer auth concerns, so they live
together here rather than being split across core/ and api/.
"""

import bcrypt
from itsdangerous import BadSignature, URLSafeTimedSerializer

from core.config import settings

SESSION_COOKIE_NAME = "hr_session"

# Namespaces this signer from any other itsdangerous use of the same
# SECRET_KEY (SQLAdmin's own session cookie is signed by Starlette's
# SessionMiddleware, a separate signer entirely — this salt is defense in
# depth, not a functional requirement).
_SESSION_SALT = "hr-session-v1"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY.get_secret_value(), salt=_SESSION_SALT)


def create_session_cookie(person_id: int) -> str:
    """Sign a stateless session cookie carrying only person_id. No server-side
    session store — see the plan's Global Constraints for why."""
    return _serializer().dumps({"person_id": person_id})


def read_session_cookie(cookie: str) -> int | None:
    """Verify and decode a session cookie, or None if missing/tampered/expired.

    max_age is read fresh from api.config.auth_settings on every call (not
    cached) so tests can monkeypatch it and so a config change takes effect
    without a process restart depending on import order.
    """
    from api.config import auth_settings

    try:
        data = _serializer().loads(cookie, max_age=auth_settings.SESSION_COOKIE_MAX_AGE_SECONDS)
    except BadSignature:
        return None
    person_id = data.get("person_id")
    return person_id if isinstance(person_id, int) else None
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_api_auth.py -v`
Expected: PASS, 7 passed.

- [ ] **Step 5: Commit**

```bash
git add api/auth.py tests/test_api_auth.py
git commit -m "feat(auth): add stateless session cookie sign/verify"
```

---

### Task 5: `api/deps.py` — session dependency and admin-gate update

**Files:**
- Modify: `api/deps.py`
- Test: `tests/test_api_deps_session.py`

**Interfaces:**
- Consumes: `api.auth.SESSION_COOKIE_NAME`, `api.auth.read_session_cookie` (Task 4).
- Produces: `get_session_user(request: Request) -> int | None`, `SessionUserDep = Annotated[int | None, Depends(get_session_user)]`. `require_admin_unless_open` gains a `session_person_id: SessionUserDep` parameter and treats a valid session as sufficient (in addition to admin creds and `ALLOW_UNAUTHENTICATED_QUERY`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_deps_session.py`:

```python
"""api/deps.py — session-cookie dependency, and require_admin_unless_open's
new "a valid session is proof enough" branch."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.auth import SESSION_COOKIE_NAME, create_session_cookie
from api.deps import get_session_user, require_admin_unless_open


@pytest.fixture
def probe_app():
    app = FastAPI()

    @app.get("/probe")
    def probe(person_id: Annotated[int | None, Depends(get_session_user)]):
        return {"person_id": person_id}

    return TestClient(app)


@pytest.fixture
def admin_gate_app(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_QUERY", False)
    app = FastAPI()

    @app.post("/gated")
    def gated(admin: Annotated[object, Depends(require_admin_unless_open)]):
        return {"admin": admin}

    return TestClient(app)


def test_no_cookie_returns_none(probe_app):
    r = probe_app.get("/probe")
    assert r.json() == {"person_id": None}


def test_valid_cookie_returns_person_id(probe_app):
    cookie = create_session_cookie(77)
    probe_app.cookies.set(SESSION_COOKIE_NAME, cookie)
    r = probe_app.get("/probe")
    assert r.json() == {"person_id": 77}


def test_garbage_cookie_returns_none(probe_app):
    probe_app.cookies.set(SESSION_COOKIE_NAME, "garbage")
    r = probe_app.get("/probe")
    assert r.json() == {"person_id": None}


def test_require_admin_unless_open_accepts_valid_session(admin_gate_app):
    cookie = create_session_cookie(77)
    admin_gate_app.cookies.set(SESSION_COOKIE_NAME, cookie)
    r = admin_gate_app.post("/gated")
    assert r.status_code == 200
    assert r.json() == {"admin": None}


def test_require_admin_unless_open_still_rejects_with_no_session_and_no_creds(admin_gate_app):
    r = admin_gate_app.post("/gated")
    assert r.status_code == 401
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_api_deps_session.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_session_user' from 'api.deps'`

- [ ] **Step 3: Implement in `api/deps.py`**

Add these imports at the top of `api/deps.py`, alongside the existing ones:

```python
from fastapi import Depends, HTTPException, Request, Security
```

(this replaces the existing `from fastapi import Depends, HTTPException, Security` line — just adds `Request`)

Add to the imports from `api.auth`:

```python
from api.auth import (
    SESSION_COOKIE_NAME,
    hash_password,
    read_session_cookie,
    verify_password,
)
```

Add this function and type alias after `_dummy_hash` and before `require_admin`:

```python
def get_session_user(request: Request) -> int | None:
    """Resolve the requester's person_id from the session cookie set by
    /auth/callback, or None if absent/invalid/expired."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie is None:
        return None
    return read_session_cookie(cookie)


SessionUserDep = Annotated[int | None, Depends(get_session_user)]
```

Replace `require_admin_unless_open` with:

```python
def require_admin_unless_open(
    session_person_id: SessionUserDep,
    credentials: HTTPBasicCredentials | None = Security(_basic_auth),
) -> AdminUser | None:
    """
    /query guard. slack_user_id in the request body selects an RBAC scope but
    is NOT proof of identity (Slack IDs are public within a workspace), so
    that path must be vouched for by admin credentials — unless
    ALLOW_UNAUTHENTICATED_QUERY explicitly opts into open access (local dev),
    or the request carries a valid Google-SSO session cookie, which IS proof
    of identity on its own (see api/routes/auth.py).
    """
    if settings.ALLOW_UNAUTHENTICATED_QUERY:
        return None
    if session_person_id is not None:
        return None
    return require_admin(credentials)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_api_deps_session.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 5: Run the full suite for regressions**

Run: `uv run pytest -q`
Expected: all previously-passing tests still pass (no test constructs `require_admin_unless_open` positionally in a way the new leading parameter would break — it's now `(session_person_id, credentials=...)` instead of `(credentials=...)`; FastAPI resolves both via dependency injection, not positional calls, so route behavior is unaffected, but confirm here).

- [ ] **Step 6: Commit**

```bash
git add api/deps.py tests/test_api_deps_session.py
git commit -m "feat(auth): add session-cookie dependency, accept it in require_admin_unless_open"
```

---

### Task 6: `resolve_scope_from_session` in `api/services/query_service.py`

**Files:**
- Modify: `api/services/query_service.py`
- Test: `tests/test_query_service_session.py`

**Interfaces:**
- Consumes: `core.rbac.resolution.resolve_context` (existing), `api.services.interfaces.ScopeResolver` (existing).
- Produces: `resolve_scope_from_session(person_id: int, resolve_ctx: ScopeResolver | None = None) -> RBACContext`. Refactors the existing error-handling tail of `resolve_scope` into a shared `_resolve_or_deny` helper (DRY — both functions need identical SQLAlchemyError→503 / None→403 handling).

- [ ] **Step 1: Write the failing test**

Create `tests/test_query_service_session.py`:

```python
"""resolve_scope_from_session — RBAC resolution for an already-authenticated
session-cookie user. No HRUser/slack_user_id lookup involved; identity is
already proven by the cookie itself (see api/deps.py:get_session_user)."""

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from api.services.query_service import resolve_scope_from_session
from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext


def test_returns_context_on_success():
    def fake_resolver(person_id):
        return RBACContext(access_level=AccessLevel.SELF, person_id=person_id)

    ctx = resolve_scope_from_session(42, resolve_ctx=fake_resolver)
    assert ctx.person_id == 42
    assert ctx.is_unrestricted is False


def test_none_from_resolver_raises_403():
    def fake_resolver(person_id):
        return None

    with pytest.raises(HTTPException) as exc_info:
        resolve_scope_from_session(42, resolve_ctx=fake_resolver)
    assert exc_info.value.status_code == 403


def test_sqlalchemy_error_raises_503():
    def fake_resolver(person_id):
        raise SQLAlchemyError("erp is down")

    with pytest.raises(HTTPException) as exc_info:
        resolve_scope_from_session(42, resolve_ctx=fake_resolver)
    assert exc_info.value.status_code == 503
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_query_service_session.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_scope_from_session'`

- [ ] **Step 3: Refactor `resolve_scope` and add `resolve_scope_from_session`**

In `api/services/query_service.py`, replace the tail of `resolve_scope` (from the `# employee_id IS person.id` comment to its `return ctx`) and add the new function. The full new content of the file's business-logic section (everything from `def resolve_scope` onward) becomes:

```python
def _resolve_or_deny(person_id: int, resolver) -> RBACContext:
    """Shared tail for resolve_scope and resolve_scope_from_session: call the
    resolver, fail closed on either an ERP error (503) or no active identity
    (403). Kept in one place so the two call sites can't drift."""
    try:
        ctx = resolver(person_id)
    except SQLAlchemyError as exc:
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


def resolve_scope(
    body: QueryRequest,
    repo: UserRepo | None = None,
    resolve_ctx: ScopeResolver | None = None,
) -> RBACContext | None:
    """Validate the request and resolve its RBAC scope. Raises HTTPException
    (400/403/503) for the fast-fail cases — always called before any streaming
    starts, so these still come back as ordinary HTTP error responses."""
    repository = repo if repo is not None else HRUserRepository
    resolver = resolve_ctx if resolve_ctx is not None else resolve_context

    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    if not body.slack_user_id:
        return None

    # DB work (user lookup) is scoped to its own short session — deliberately
    # NOT held open across the agent call, which can take up to ~15s. Holding
    # one session for the whole request would tie up a pool connection for
    # that entire span instead of just the few DB round-trips that actually
    # need it.
    with db_session() as session:
        hr_user = repository.get_by_slack_user_id(session, body.slack_user_id)

    if not hr_user:
        raise HTTPException(
            status_code=403,
            detail="User not registered. Ask your HR admin to add your Slack account.",
        )

    # employee_id IS person.id — see core/rbac/models.py.
    return _resolve_or_deny(hr_user.employee_id, resolver)


def resolve_scope_from_session(
    person_id: int,
    resolve_ctx: ScopeResolver | None = None,
) -> RBACContext:
    """RBAC resolution for a request carrying a valid Google-SSO session
    cookie. Identity is already proven by the cookie (api/deps.py's
    get_session_user); this only resolves the access level, with the same
    fail-closed error handling as resolve_scope."""
    resolver = resolve_ctx if resolve_ctx is not None else resolve_context
    return _resolve_or_deny(person_id, resolver)


def run_agent(
    query: str,
    rbac_ctx: RBACContext | None,
    agent: AgentRunner | None = None,
) -> AgentQueryResult:
    """The slow part — runs on core.executor.agent_executor, not the request
    thread. Raises on failure; the caller (api/routes/query.py's SSE
    generator) turns that into an `event: error` instead of an HTTP 500,
    since by the time this runs the response has already started streaming."""
    runner = agent if agent is not None else agent_query
    return runner(query, rbac_ctx=rbac_ctx)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_query_service_session.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Run the existing query_service tests for regressions**

Run: `uv run pytest tests/test_query_service_injection.py -v`
Expected: PASS, unchanged (the refactor preserves `resolve_scope`'s external behavior exactly).

- [ ] **Step 6: Commit**

```bash
git add api/services/query_service.py tests/test_query_service_session.py
git commit -m "feat(auth): add resolve_scope_from_session, DRY the error-handling tail"
```

---

### Task 7: Wire `/query` to try the session cookie first

**Files:**
- Modify: `api/routes/query.py`
- Test: `tests/test_query_route_session.py`

**Interfaces:**
- Consumes: `api.deps.SessionUserDep` (Task 5), `api.services.query_service.resolve_scope_from_session` (Task 6).
- Produces: no new public names; `run_query`'s behavior gains a session-first branch.

- [ ] **Step 1: Write the failing test**

Create `tests/test_query_route_session.py`:

```python
"""POST /query — session-cookie identity takes priority; falls through to
the existing admin/slack_user_id path when no valid session cookie is
present. Uses a bare FastAPI app + the real router, agent mocked out."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import SESSION_COOKIE_NAME, create_session_cookie
from api.routes import query as query_route
from core.agent import AgentQueryResult
from core.rbac.access import AccessLevel
from core.rbac.context import RBACContext


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(query_route.settings, "ALLOW_UNAUTHENTICATED_QUERY", True) if hasattr(
        query_route, "settings"
    ) else None
    from core.config import settings

    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_QUERY", True)
    app = FastAPI()
    app.include_router(query_route.router)
    return TestClient(app)


def _sse_result(response):
    for block in response.text.split("\n\n"):
        if block.startswith("event: answer") or block.startswith("event: error"):
            import json

            for line in block.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:") :].strip())
    raise AssertionError(response.text)


def test_session_cookie_used_when_present(client):
    captured = {}

    def fake_resolve_scope_from_session(person_id, resolve_ctx=None):
        captured["person_id"] = person_id
        return RBACContext.unrestricted()

    def fake_run_agent(query, rbac_ctx):
        captured["ctx"] = rbac_ctx
        return AgentQueryResult(
            answer="ok", tables_accessed="", schema_rag_ms=0, agent_ms=0, total_ms=0
        )

    cookie = create_session_cookie(999)
    client.cookies.set(SESSION_COOKIE_NAME, cookie)

    with (
        patch("api.routes.query.resolve_scope_from_session", fake_resolve_scope_from_session),
        patch("api.routes.query.run_agent", fake_run_agent),
    ):
        r = client.post("/query", json={"query": "how many staff?"})

    assert r.status_code == 200
    assert captured["person_id"] == 999
    assert _sse_result(r)["answer"] == "ok"


def test_falls_through_to_resolve_scope_without_cookie(client):
    captured = {}

    def fake_resolve_scope(body, repo=None, resolve_ctx=None):
        captured["called"] = True
        return None

    def fake_run_agent(query, rbac_ctx):
        return AgentQueryResult(
            answer="ok2", tables_accessed="", schema_rag_ms=0, agent_ms=0, total_ms=0
        )

    with (
        patch("api.routes.query.resolve_scope", fake_resolve_scope),
        patch("api.routes.query.run_agent", fake_run_agent),
    ):
        r = client.post("/query", json={"query": "how many staff?"})

    assert r.status_code == 200
    assert captured["called"] is True
    assert _sse_result(r)["answer"] == "ok2"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_query_route_session.py -v`
Expected: FAIL — `test_session_cookie_used_when_present` fails because `resolve_scope_from_session` is not yet imported/used in `api/routes/query.py` (the patch target doesn't exist), so `resolve_scope` gets called instead and `captured["person_id"]` is never set.

- [ ] **Step 3: Update `api/routes/query.py`**

Replace the file's imports and `run_query` function. Change:

```python
from api.deps import OptionalAdminDep
from api.schemas.query import QueryRequest, QueryResponse
from api.services.query_service import resolve_scope, run_agent
from core.executor import agent_executor
from core.rbac.context import RBACContext
```

to:

```python
from api.deps import OptionalAdminDep, SessionUserDep
from api.schemas.query import QueryRequest, QueryResponse
from api.services.query_service import resolve_scope, resolve_scope_from_session, run_agent
from core.executor import agent_executor
from core.rbac.context import RBACContext
```

Replace `run_query` with:

```python
@router.post("/query")
async def run_query(
    body: QueryRequest,
    admin: OptionalAdminDep,
    session_person_id: SessionUserDep,
) -> StreamingResponse:
    """
    Natural-language HR query endpoint — streamed over Server-Sent Events.

    Identity resolution, in priority order:
    1. A valid Google-SSO session cookie (api/routes/auth.py) — the requester
       IS this person_id, proven by the cookie; RBAC scope is resolved fresh
       via resolve_scope_from_session.
    2. Otherwise, today's existing path: admin HTTP Basic auth vouching for
       an optional slack_user_id, or ALLOW_UNAUTHENTICATED_QUERY=true for
       local dev — require_admin_unless_open has already enforced this by
       the time this function runs, so no further auth check is needed here.

    Response is `text/event-stream`: an immediate `status` event, periodic
    `: heartbeat` comments while the agent runs, then exactly one of
    `answer` or `error`.
    """
    if session_person_id is not None:
        rbac_ctx = await run_in_threadpool(resolve_scope_from_session, session_person_id)
    else:
        rbac_ctx = await run_in_threadpool(resolve_scope, body)
    return StreamingResponse(_stream_query(body.query, rbac_ctx), media_type="text/event-stream")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_query_route_session.py -v`
Expected: PASS, 2 passed.

- [ ] **Step 5: Run the full suite for regressions**

Run: `uv run pytest -q`
Expected: all previously-passing tests still pass, including `tests/test_e2e.py`'s `TestQueryAuthenticated`/`TestQueryIdentityForgery` classes (none of them send a session cookie, so they all take the unchanged `else` branch).

- [ ] **Step 6: Commit**

```bash
git add api/routes/query.py tests/test_query_route_session.py
git commit -m "feat(auth): try the session cookie before the admin/slack path in /query"
```

---

### Task 8: `api/routes/auth.py` — login, callback, logout

**Files:**
- Create: `api/routes/auth.py`
- Test: `tests/test_auth_routes.py`

**Interfaces:**
- Consumes: `api.config.auth_settings` (Task 3), `api.auth.create_session_cookie`/`SESSION_COOKIE_NAME` (Task 4), `core.rbac.resolution.get_resolver` (existing).
- Produces: `router: APIRouter` with `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auth_routes.py`:

```python
"""GET /auth/login, GET /auth/callback, POST /auth/logout.

Authlib's actual Google round-trip is mocked out (authorize_redirect,
authorize_access_token) — these tests exercise this app's own logic: the
hd/email_verified checks, the ERP lookup, and cookie issuance.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from api.auth import SESSION_COOKIE_NAME, read_session_cookie
from api.routes import auth as auth_route
from core.rbac.erp_identity import ErpIdentity


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-only-not-for-production")
    app.include_router(auth_route.router)
    return TestClient(app, follow_redirects=False)


def test_login_redirects_to_google(client):
    with patch.object(
        auth_route.oauth.google, "authorize_redirect", new=AsyncMock()
    ) as mock_redirect:
        from starlette.responses import RedirectResponse

        mock_redirect.return_value = RedirectResponse("https://accounts.google.com/fake", 302)
        r = client.get("/auth/login")
    assert r.status_code == 302
    assert mock_redirect.call_args.kwargs.get("hd") == "arbisoft.com"


def test_callback_rejects_wrong_domain(client):
    token = {"userinfo": {"email": "eve@gmail.com", "email_verified": True, "hd": "gmail.com"}}
    with patch.object(auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)):
        r = client.get("/auth/callback")
    assert r.status_code == 403
    assert SESSION_COOKIE_NAME not in r.cookies


def test_callback_rejects_unverified_email(client):
    token = {
        "userinfo": {"email": "alice@arbisoft.com", "email_verified": False, "hd": "arbisoft.com"}
    }
    with patch.object(auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)):
        r = client.get("/auth/callback")
    assert r.status_code == 403
    assert SESSION_COOKIE_NAME not in r.cookies


def test_callback_not_registered_in_erp(client):
    token = {
        "userinfo": {"email": "alice@arbisoft.com", "email_verified": True, "hd": "arbisoft.com"}
    }
    with (
        patch.object(auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)),
        patch("api.routes.auth.get_resolver") as mock_get_resolver,
    ):
        mock_get_resolver.return_value.by_email.return_value = None
        r = client.get("/auth/callback")
    assert r.status_code == 403
    assert SESSION_COOKIE_NAME not in r.cookies
    assert "not registered" in r.text.lower()


def test_callback_success_sets_cookie(client):
    token = {
        "userinfo": {"email": "alice@arbisoft.com", "email_verified": True, "hd": "arbisoft.com"}
    }
    identity = ErpIdentity(person_id=100, auth_user_id=500, group_ids=frozenset())
    with (
        patch.object(auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)),
        patch("api.routes.auth.get_resolver") as mock_get_resolver,
    ):
        mock_get_resolver.return_value.by_email.return_value = identity
        r = client.get("/auth/callback")
    assert r.status_code == 200
    assert SESSION_COOKIE_NAME in r.cookies
    assert read_session_cookie(r.cookies[SESSION_COOKIE_NAME]) == 100


def test_callback_erp_error_returns_503(client):
    token = {
        "userinfo": {"email": "alice@arbisoft.com", "email_verified": True, "hd": "arbisoft.com"}
    }
    from sqlalchemy.exc import SQLAlchemyError

    with (
        patch.object(auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)),
        patch("api.routes.auth.get_resolver") as mock_get_resolver,
    ):
        mock_get_resolver.return_value.by_email.side_effect = SQLAlchemyError("down")
        r = client.get("/auth/callback")
    assert r.status_code == 503


def test_logout_clears_cookie(client):
    r = client.post("/auth/logout")
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie
    # A cleared cookie is set with an immediate/expired Max-Age.
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_auth_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.routes.auth'`

- [ ] **Step 3: Implement `api/routes/auth.py`**

```python
"""Google Workspace SSO login — GET /auth/login, GET /auth/callback,
POST /auth/logout.

Restricted to api.config.auth_settings.GOOGLE_WORKSPACE_DOMAIN, enforced
server-side against the ID token's `hd` claim (Google only includes `hd`
for real Workspace accounts, so a personal gmail.com login simply won't
carry it — this is a real check, not just a login-screen hint).

No server-side session store: a successful login signs a stateless cookie
carrying only person_id (api/auth.py). Revocation freshness comes from
core.rbac.resolution.resolve_context's existing RBAC_CACHE_TTL_SECONDS
re-check of person.is_active on every /query call, not from anything here.
"""

import structlog
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import SQLAlchemyError

from api.auth import SESSION_COOKIE_NAME, create_session_cookie
from api.config import auth_settings
from core.rbac.resolution import get_resolver

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=auth_settings.GOOGLE_CLIENT_ID,
    client_secret=auth_settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email"},
)


@router.get("/login")
async def login(request: Request):
    """Redirect to Google. hd=<domain> narrows the account chooser to the
    company Workspace — a UI hint only; the real enforcement is in
    /auth/callback checking the returned ID token's hd claim."""
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(
        request, redirect_uri, hd=auth_settings.GOOGLE_WORKSPACE_DOMAIN
    )


def _reject(reason: str) -> HTMLResponse:
    logger.warning("sso_login_rejected", reason=reason)
    return HTMLResponse(f"<p>{reason}</p>", status_code=403)


@router.get("/callback", name="auth_callback")
async def callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    claims = token.get("userinfo", {})

    email = claims.get("email")
    if not email or not claims.get("email_verified"):
        return _reject("Your Google account's email is not verified.")

    if claims.get("hd") != auth_settings.GOOGLE_WORKSPACE_DOMAIN:
        return _reject(
            f"Please log in with your {auth_settings.GOOGLE_WORKSPACE_DOMAIN} account."
        )

    try:
        identity = get_resolver().by_email(email)
    except SQLAlchemyError as exc:
        logger.warning("sso_erp_lookup_failed", error=str(exc))
        return HTMLResponse(
            "<p>Can't verify your account right now. Please try again shortly.</p>",
            status_code=503,
        )

    if identity is None:
        return _reject("You're not registered in the ERP. Contact HR.")

    cookie_value = create_session_cookie(identity.person_id)

    if auth_settings.POST_LOGIN_REDIRECT_URL:
        response = RedirectResponse(auth_settings.POST_LOGIN_REDIRECT_URL, status_code=302)
    else:
        response = HTMLResponse("<p>Logged in. You can close this tab.</p>")

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        max_age=auth_settings.SESSION_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout():
    response = HTMLResponse("<p>Logged out.</p>")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_auth_routes.py -v`
Expected: PASS, 7 passed.

- [ ] **Step 5: Commit**

```bash
git add api/routes/auth.py tests/test_auth_routes.py
git commit -m "feat(auth): add Google Workspace SSO login/callback/logout routes"
```

---

### Task 9: Wire `api/main.py` — SessionMiddleware, CORS credentials, router, deprecation notes

**Files:**
- Modify: `api/main.py`
- Modify: `adapters/slack.py` (module docstring only)
- Modify: `core/rbac/models.py` (`HRUser` docstring only)
- Modify: `api/routes/users.py`, `api/services/user_service.py`, `api/schemas/users.py`, `api/admin.py` (docstring/comment only)
- Test: `tests/test_auth_main_wiring.py`

**Interfaces:**
- Consumes: `api.routes.auth.router` (Task 8), `api.config.auth_settings` (Task 3).
- Produces: no new public names — wires everything into the running app.

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth_main_wiring.py`:

```python
"""api/main.py wiring: SessionMiddleware present (Authlib needs it for OAuth
state), CORS allows credentials, and the auth router is mounted."""

import importlib

import pytest


def test_auth_router_is_mounted():
    import api.main as main_mod

    importlib.reload(main_mod)
    paths = {route.path for route in main_mod.app.routes}
    assert "/auth/login" in paths
    assert "/auth/callback" in paths
    assert "/auth/logout" in paths


def test_session_middleware_is_present():
    import api.main as main_mod

    importlib.reload(main_mod)
    from starlette.middleware.sessions import SessionMiddleware

    assert any(m.cls is SessionMiddleware for m in main_mod.app.user_middleware)


def test_cors_allows_credentials():
    import api.main as main_mod

    importlib.reload(main_mod)
    from starlette.middleware.cors import CORSMiddleware

    cors = next(m for m in main_mod.app.user_middleware if m.cls is CORSMiddleware)
    assert cors.kwargs.get("allow_credentials") is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_auth_main_wiring.py -v`
Expected: FAIL — all 3 assertions fail (router not mounted, no SessionMiddleware, no `allow_credentials`).

- [ ] **Step 3: Update `api/main.py`**

Add to the imports (alongside `from api.routes import health, query, slack, users`):

```python
from api.routes import auth, health, query, slack, users
```

Add a new import for `SessionMiddleware`, alongside the other `starlette.middleware` imports:

```python
from starlette.middleware.sessions import SessionMiddleware
```

Update the `CORSMiddleware` block to add `allow_credentials=True`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    # Only the methods/headers the API actually uses — the admin panel is
    # browsed same-origin and isn't affected by CORS at all.
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

# Required by Authlib's Starlette OAuth client, which stores the OAuth
# state/nonce in request.session during the login redirect round-trip
# (api/routes/auth.py). Separate from SQLAdmin's own internal
# SessionMiddleware, which sqladmin.Admin() scopes only to its /admin
# sub-app — this one covers the rest of the app (/auth/*).
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY.get_secret_value(),
    same_site="lax",
    https_only=not settings.DEBUG,
)
```

Add `allow_credentials=True` note: since `CORS_ALLOW_ORIGINS=*` is already forbidden in production by this app's own guard (`core/config.py`), no new guard is needed — but confirm this in Step 5.

Add the router include:

```python
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(query.router)
app.include_router(users.router)
app.include_router(slack.router)
```

- [ ] **Step 4: Mark the Slack/HRUser code paths deprecated (comments only, no behavior change)**

In `adapters/slack.py`, change the module docstring's opening line from:

```python
"""
Slack adapter — receives Events API payloads, enforces RBAC, and posts Block Kit replies.
```

to:

```python
"""
DEPRECATED — superseded by Google SSO login (api/routes/auth.py). Kept as-is
for now; may be removed later. New end-user access should go through the
session-cookie path in api/routes/query.py, not Slack registration.

Slack adapter — receives Events API payloads, enforces RBAC, and posts Block Kit replies.
```

In `core/rbac/models.py`, add one line to the `HRUser` class docstring area — insert directly above the `class HRUser(Base):` line:

```python
# DEPRECATED — this table/model registered Slack users for the old
# Slack-identity RBAC path. Superseded by Google SSO login
# (api/routes/auth.py), which resolves identity straight from the ERP with
# no local registration step. Kept as-is; may be removed later.
```

In `api/routes/users.py`, `api/services/user_service.py`, and `api/schemas/users.py`, add this one-line comment at the very top of each file (above any existing docstring/imports):

```python
# DEPRECATED — registers users for the old Slack-identity RBAC path, superseded
# by Google SSO login (api/routes/auth.py). Kept as-is; may be removed later.
```

In `api/admin.py`, add the same comment directly above the `class HRUserAdmin(ModelView, model=HRUser):` line.

- [ ] **Step 5: Run to verify the new tests pass**

Run: `uv run pytest tests/test_auth_main_wiring.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 6: Run the full suite for regressions**

Run: `uv run pytest -q`
Expected: all tests still pass. Pay particular attention to `tests/test_e2e.py` — its `client` fixture reloads `api.main`, so it now also picks up `SessionMiddleware` and the mounted `/auth/*` routes; nothing in that file calls those routes, so this should be a clean pass-through.

- [ ] **Step 7: Commit**

```bash
git add api/main.py adapters/slack.py core/rbac/models.py api/routes/users.py api/services/user_service.py api/schemas/users.py api/admin.py tests/test_auth_main_wiring.py
git commit -m "feat(auth): wire SSO routes into the app, mark Slack/HRUser path deprecated"
```

---

### Task 10: End-to-end test through the real app

**Files:**
- Modify: `tests/test_e2e.py`

**Interfaces:**
- Consumes: everything from Tasks 1-9.
- Produces: one new test class proving the full login → `/query` flow works through the actual FastAPI app (not just unit-level route tests).

- [ ] **Step 1: Add an `email` column to the e2e fixture's ERP stand-in**

In `tests/test_e2e.py`'s `client` fixture, find:

```python
            conn.execute(
                sqlalchemy.text(
                    "CREATE TABLE IF NOT EXISTS auth_user "
                    "(id INTEGER PRIMARY KEY, is_active BOOLEAN NOT NULL)"
                )
            )
```

Replace with:

```python
            conn.execute(
                sqlalchemy.text(
                    "CREATE TABLE IF NOT EXISTS auth_user (id INTEGER PRIMARY KEY, "
                    "is_active BOOLEAN NOT NULL, email VARCHAR(254) NOT NULL DEFAULT '')"
                )
            )
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_e2e.py`:

```python
# ---------------------------------------------------------------------------
# Google SSO login — end-to-end through the real app
# ---------------------------------------------------------------------------


class TestGoogleSSOLoginE2E:
    """Authlib's Google round-trip is mocked; everything after it (ERP
    lookup, cookie issuance, /query using that cookie) runs for real against
    the client fixture's test SQLite ERP stand-in."""

    @pytest.fixture
    def sso_user(self, client):
        """Insert an auth_user/person row with a known email, matching the
        e2e fixture's ERP stand-in schema."""
        import sqlalchemy

        from core.config import settings

        engine = sqlalchemy.create_engine(settings.DATABASE_URL)
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    "INSERT INTO auth_user (id, is_active, email) VALUES "
                    "(7001, 1, 'sso.tester@arbisoft.com')"
                )
            )
            conn.execute(
                sqlalchemy.text("INSERT INTO person (id, user_id, is_active) VALUES (7002, 7001, 1)")
            )
        engine.dispose()
        return "sso.tester@arbisoft.com"

    def test_login_then_query_uses_session_cookie(self, client, sso_user):
        from unittest.mock import AsyncMock, patch

        token = {
            "userinfo": {
                "email": sso_user,
                "email_verified": True,
                "hd": "arbisoft.com",
            }
        }
        import api.routes.auth as auth_route

        with patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ):
            callback_response = client.get("/auth/callback")
        assert callback_response.status_code == 200

        r = client.post("/query", json={"query": "How many employees?"})
        assert r.status_code == 200
        assert _sse_result(r)["answer"] == MOCK_ANSWER

    def test_unregistered_email_rejected(self, client):
        from unittest.mock import AsyncMock, patch

        token = {
            "userinfo": {
                "email": "not.in.erp@arbisoft.com",
                "email_verified": True,
                "hd": "arbisoft.com",
            }
        }
        import api.routes.auth as auth_route

        with patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ):
            r = client.get("/auth/callback")
        assert r.status_code == 403

    def test_wrong_domain_rejected(self, client):
        from unittest.mock import AsyncMock, patch

        token = {
            "userinfo": {"email": "eve@gmail.com", "email_verified": True, "hd": "gmail.com"}
        }
        import api.routes.auth as auth_route

        with patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ):
            r = client.get("/auth/callback")
        assert r.status_code == 403

    def test_logout_then_query_falls_back_to_admin_gate(self, client, sso_user):
        from unittest.mock import AsyncMock, patch

        token = {
            "userinfo": {"email": sso_user, "email_verified": True, "hd": "arbisoft.com"}
        }
        import api.routes.auth as auth_route

        with patch.object(
            auth_route.oauth.google, "authorize_access_token", new=AsyncMock(return_value=token)
        ):
            client.get("/auth/callback")
        client.post("/auth/logout")

        # ALLOW_UNAUTHENTICATED_QUERY=True in this fixture, so the fallback
        # path succeeds without admin creds too — proves the session cookie
        # is genuinely gone, not that auth broke.
        r = client.post("/query", json={"query": "How many employees?"})
        assert r.status_code == 200
```

- [ ] **Step 3: Run to verify it fails first**

Run: `uv run pytest tests/test_e2e.py::TestGoogleSSOLoginE2E -v`
Expected: FAIL initially if Tasks 1-9 aren't complete yet in this environment; once they are (which they will be, since this task runs after them), this instead serves as the regression check — run it and confirm PASS.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_e2e.py::TestGoogleSSOLoginE2E -v`
Expected: PASS, 4 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass, no regressions anywhere.

- [ ] **Step 6: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: add end-to-end coverage for the Google SSO login flow"
```

---

### Task 11: Frontend — sign-in link and credentialed requests

**Files:**
- Modify: `index.html`

**Interfaces:**
- Consumes: `GET /auth/login`, `POST /query` (now cookie-aware).
- Produces: no new backend surface — a "Sign in with Google" link in the header, and `credentials: 'include'` on the existing fetch calls so a session cookie set by the backend actually gets sent back.

- [ ] **Step 1: Add `credentials: 'include'` to the query fetch**

In `index.html`, find `postQuery`:

```javascript
  function postQuery(base, q) {
    const headers = { 'Content-Type': 'application/json' };
    if (creds) headers['Authorization'] = 'Basic ' + btoa(creds.user + ':' + creds.pass);
    return fetch(base + '/query', {
      method: 'POST',
      headers,
      body: JSON.stringify({ query: q }),
    });
  }
```

Replace with:

```javascript
  function postQuery(base, q) {
    const headers = { 'Content-Type': 'application/json' };
    if (creds) headers['Authorization'] = 'Basic ' + btoa(creds.user + ':' + creds.pass);
    return fetch(base + '/query', {
      method: 'POST',
      headers,
      credentials: 'include', // send the Google-SSO session cookie, if any
      body: JSON.stringify({ query: q }),
    });
  }
```

- [ ] **Step 2: Add a "Sign in with Google" link to the header**

Find the `<header>` block:

```html
<header>
  <h1>HR Assistant</h1>
  <span>local</span>
  <div id="status-dot" title="API health"></div>
</header>
```

Replace with:

```html
<header>
  <h1>HR Assistant</h1>
  <span>local</span>
  <div id="status-dot" title="API health"></div>
  <a id="sso-login-link" href="#" style="margin-left:auto;font-size:13px;">Sign in with Google</a>
</header>
```

- [ ] **Step 3: Wire the link to the configured API URL, and surface a not-registered error**

In the `<script>` block, right after the existing `const apiUrl = document.getElementById('api-url');` line, add:

```javascript
  const ssoLink = document.getElementById('sso-login-link');
  function updateSsoLink() {
    ssoLink.href = apiUrl.value.replace(/\/+$/, '') + '/auth/login';
  }
  updateSsoLink();
  apiUrl.addEventListener('input', updateSsoLink);

  // Surfaced by the backend redirecting with ?error=not_registered, if
  // POST_LOGIN_REDIRECT_URL is configured to point back at this page.
  const params = new URLSearchParams(window.location.search);
  if (params.get('error') === 'not_registered') {
    chat.innerHTML = '<p style="color:#b00">Your Google account is not registered in the HR system. Contact HR.</p>';
  }
```

- [ ] **Step 4: Manual verification**

This file has no test harness — verify by hand:

1. `uv run python -m api.main` (or however the dev server is normally started) to boot the backend.
2. Open `index.html` directly in a browser (e.g. `open index.html` on macOS, or serve it with `python -m http.server` from the repo root and visit it).
3. Confirm the "Sign in with Google" link's `href` updates when the API URL field changes.
4. Confirm clicking it navigates to `<api-url>/auth/login` (a 500 at Google's end without real `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` configured in `.env` is expected in a dev environment with no real OAuth app registered — this step only verifies the frontend wiring, not a live Google login).

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(auth): add Google sign-in link and credentialed /query requests"
```

---

## Post-implementation notes

- **`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` must be set in `.env`** for login to work against real Google infrastructure — create an OAuth client at console.cloud.google.com, authorized redirect URI `<your-api-url>/auth/callback`. Nothing in this plan can test against real Google; every test mocks `oauth.google.authorize_access_token`.
- **`POST_LOGIN_REDIRECT_URL`** defaults to empty (inline HTML confirmation). Once the frontend has a fixed, deployed origin, set this to that origin so `/auth/callback` redirects the browser straight back into the app instead of showing a static confirmation page.
- **Open-redirect risk deliberately not addressed**: a dynamic `?next=` param (redirect back to whatever page initiated login) would be a better UX than a fixed `POST_LOGIN_REDIRECT_URL`, but needs same-origin/allowlist validation to avoid becoming an open redirect. Out of scope here; flagged for whoever picks up the fixed-frontend-origin work above.
- **`GOOGLE_WORKSPACE_DOMAIN` covers only Google Workspace accounts.** A `gmail.com` personal account can never carry an `hd` claim, so it's rejected regardless of what email address it uses — this is the correct behavior, not a bug, even if someone's ERP email happens to match a personal account they're logged into.
