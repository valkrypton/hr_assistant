import threading
import time

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Admin login lockout
#
# In-process failed-attempt counter keyed by username, mirroring the
# _seen_events TTL-dict pattern in adapters/slack.py — single-worker-only,
# same documented limitation. Deliberately just a lockout signal, not an
# attempt log: it doesn't record who/when/from-where beyond the rolling
# timestamp list needed to expire lockouts, staying clear of the
# audit-logging territory SPEC.md FR-6 says needs a separate product
# decision. Applied to both the SQLAdmin panel login (api/main.py) and the
# HTTP Basic /users + /query path (api/deps.py.require_admin).
# ---------------------------------------------------------------------------

_LOCKOUT_THRESHOLD = 5
_LOCKOUT_WINDOW_SECONDS = 300  # 5 minutes

_failed_attempts: dict[str, list[float]] = {}
_lock = threading.Lock()


def _recent_attempts(username: str, now: float) -> list[float]:
    return [t for t in _failed_attempts.get(username, []) if now - t < _LOCKOUT_WINDOW_SECONDS]


def is_locked_out(username: str) -> bool:
    """True if `username` has hit the failed-attempt threshold within the
    rolling window. Also implicitly evicts expired attempts for this user."""
    now = time.monotonic()
    with _lock:
        attempts = _recent_attempts(username, now)
        _failed_attempts[username] = attempts
        return len(attempts) >= _LOCKOUT_THRESHOLD


def record_failed_login(username: str) -> None:
    now = time.monotonic()
    with _lock:
        attempts = _recent_attempts(username, now)
        attempts.append(now)
        _failed_attempts[username] = attempts


def clear_failed_logins(username: str) -> None:
    with _lock:
        _failed_attempts.pop(username, None)
