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
