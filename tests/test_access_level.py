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
