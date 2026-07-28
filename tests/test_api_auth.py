"""api/auth.py — password hashing (existing) + session cookie sign/verify (new)."""

import time

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
    # Flip a character in the middle, not the last character: base64url's
    # final character can encode as few as 2 significant bits (padding-
    # dependent), so occasionally two different last characters decode to
    # the same underlying bytes — flipping it is not guaranteed to change
    # the signature at all, making that variant of this test flaky. A
    # middle character always sits in a full 6-bit group.
    cookie = create_session_cookie(42)
    mid = len(cookie) // 2
    tampered = cookie[:mid] + ("a" if cookie[mid] != "a" else "b") + cookie[mid + 1 :]
    assert read_session_cookie(tampered) is None


def test_garbage_cookie_returns_none():
    assert read_session_cookie("not-a-real-cookie") is None


def test_expired_cookie_returns_none(monkeypatch):
    from api.config import auth_settings

    # itsdangerous rounds elapsed time to whole seconds and expires only when
    # age > max_age, so a 1.5s sleep against max_age=1 is a coin flip
    # (age could floor to 1, and 1 > 1 is False). 2.2s against max_age=1
    # guarantees age >= 2, clearing that boundary reliably.
    monkeypatch.setattr(auth_settings, "SESSION_COOKIE_MAX_AGE_SECONDS", 1)
    cookie = create_session_cookie(42)
    time.sleep(2.2)
    assert read_session_cookie(cookie) is None


def test_cookie_for_different_person_ids_differs():
    assert create_session_cookie(1) != create_session_cookie(2)
