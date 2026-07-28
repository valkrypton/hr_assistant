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
    session store — see the SSO login plan's Global Constraints for why."""
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
