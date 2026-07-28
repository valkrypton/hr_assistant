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
