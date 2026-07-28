"""api/main.py wiring: SessionMiddleware present (Authlib needs it for OAuth
state), CORS allows credentials, and the auth router is mounted."""

import importlib


def _all_paths(app) -> set[str]:
    """Flatten app.routes into path strings.

    FastAPI wraps each include_router() call in a fastapi.routing._IncludedRouter
    that holds the real APIRoute objects on .original_router.routes rather than
    exposing them directly on app.routes — so a plain {r.path for r in app.routes}
    misses everything mounted via include_router (SQLAdmin's /admin mount is a
    plain starlette.routing.Mount and does expose .path directly).
    """
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(path)
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            paths.update(r.path for r in original_router.routes if hasattr(r, "path"))
    return paths


def test_auth_router_is_mounted():
    import api.main as main_mod

    importlib.reload(main_mod)
    paths = _all_paths(main_mod.app)
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
