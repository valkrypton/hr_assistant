"""
Unit tests for the admin login lockout in core/auth.py.

Deliberately a pure in-process counter (no persistence, no per-attempt log —
see core/auth.py's module docstring for why), so these tests exercise the
module's dict-based state directly rather than going through the full
require_admin/AdminAuth HTTP paths.
"""

import core.auth as auth_mod


def _reset(username: str) -> None:
    auth_mod.clear_failed_logins(username)


class TestLockout:
    def test_not_locked_out_before_any_failures(self):
        _reset("alice")
        assert auth_mod.is_locked_out("alice") is False

    def test_locks_out_after_threshold_failures(self):
        _reset("bob")
        for _ in range(auth_mod._LOCKOUT_THRESHOLD):
            auth_mod.record_failed_login("bob")
        assert auth_mod.is_locked_out("bob") is True

    def test_not_locked_out_below_threshold(self):
        _reset("carol")
        for _ in range(auth_mod._LOCKOUT_THRESHOLD - 1):
            auth_mod.record_failed_login("carol")
        assert auth_mod.is_locked_out("carol") is False

    def test_clear_failed_logins_resets_lockout(self):
        _reset("dave")
        for _ in range(auth_mod._LOCKOUT_THRESHOLD):
            auth_mod.record_failed_login("dave")
        assert auth_mod.is_locked_out("dave") is True

        auth_mod.clear_failed_logins("dave")

        assert auth_mod.is_locked_out("dave") is False

    def test_lockout_is_per_username(self):
        _reset("eve")
        _reset("frank")
        for _ in range(auth_mod._LOCKOUT_THRESHOLD):
            auth_mod.record_failed_login("eve")

        assert auth_mod.is_locked_out("eve") is True
        assert auth_mod.is_locked_out("frank") is False

    def test_old_attempts_outside_window_dont_count(self, monkeypatch):
        _reset("grace")
        t = [1000.0]
        monkeypatch.setattr(auth_mod.time, "monotonic", lambda: t[0])

        for _ in range(auth_mod._LOCKOUT_THRESHOLD):
            auth_mod.record_failed_login("grace")
        assert auth_mod.is_locked_out("grace") is True

        # Jump past the rolling window — those attempts should now expire.
        t[0] += auth_mod._LOCKOUT_WINDOW_SECONDS + 1

        assert auth_mod.is_locked_out("grace") is False
