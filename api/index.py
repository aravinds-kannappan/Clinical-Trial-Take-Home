"""Vercel entrypoint: exposes the FastAPI app as a serverless function."""

from app.main import app as _fastapi_app

_PREFIX = "/api/index"


async def app(scope, receive, send):
    """Strip the function path prefix if the platform forwards it, then delegate to FastAPI."""
    if scope.get("type") == "http" and scope.get("path", "").startswith(_PREFIX):
        scope = dict(scope)
        scope["path"] = scope["path"][len(_PREFIX):] or "/"
        scope["raw_path"] = scope["path"].encode()
    await _fastapi_app(scope, receive, send)
