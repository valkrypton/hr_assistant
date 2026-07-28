"""ErpIdentityResolver — the only module that reads ERP auth tables."""

import pytest
import sqlalchemy

from core.rbac.erp_identity import ErpIdentity, ErpIdentityResolver


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
                "CREATE TABLE auth_user (id INTEGER PRIMARY KEY, is_active INTEGER NOT NULL, "
                "email VARCHAR(254) NOT NULL DEFAULT '')"
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
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO auth_user (id, is_active, email) VALUES "
                "(500, 1, 'alice@arbisoft.com'), (501, 1, 'bob@arbisoft.com'), "
                "(502, 0, 'inactive.user@arbisoft.com')"
            )
        )
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO person VALUES (100, 500, 1), (101, 501, 1), "
                "(102, 502, 1), (103, 500, 0)"
            )
        )
        # person 100 -> HR group; person 101 -> no groups
        conn.execute(
            sqlalchemy.text("INSERT INTO auth_user_groups VALUES (1, 500, 12), (2, 500, 9)")
        )
        conn.execute(
            sqlalchemy.text("INSERT INTO auth_group VALUES (12, 'Pod'), (13, 'Management')")
        )
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
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO auth_user (id, is_active, email) VALUES "
                "(600, 1, 'new.hire@arbisoft.com')"
            )
        )
        conn.execute(sqlalchemy.text("INSERT INTO person VALUES (200, 600, 1)"))
    assert resolver.by_person_id(200) is not None


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
