import os

# Satisfy the production config guards in core.config (they run at import time
# with DEBUG=false). Tests that need different values override settings.* at
# runtime; these just let the module import cleanly.
os.environ.setdefault("SECRET_KEY", "test-only-not-for-production")
os.environ.setdefault("APP_DATABASE_URL", "sqlite:///./data/app.db")
os.environ.setdefault("CORS_ALLOW_ORIGINS", "http://localhost")
