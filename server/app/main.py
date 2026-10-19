"""FastAPI app factory and the `odysseus-edit` console entry point.

`create_app()` wires the API routers, then mounts `web/` at `/` so the
frontend is served from the same origin. Routes are registered before the
static mount, so `/api/*` always wins.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app import __version__
from app.config import REPO_ROOT, ConfigError, Settings, load_settings
from app.middleware import (
    DEFAULT_MAX_BODY_BYTES,
    LOOPBACK_HOSTS,
    BodySizeLimitMiddleware,
    LocalOriginMiddleware,
)
from app.routers import include_routers

logger = logging.getLogger("odysseus_edit")


class NoCacheStaticFiles(StaticFiles):
    """Static files that browsers must revalidate (the JS has no hashed names).

    `no-cache` still allows a cached copy; StaticFiles answers the
    revalidation with 304 via ETag / Last-Modified.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response


def _default_web_dir() -> Path:
    override = os.environ.get("ODYSSEUS_EDIT_WEB_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_absolute() else REPO_ROOT / path
    return REPO_ROOT / "web"


def _max_body_bytes() -> int:
    raw = os.environ.get("ODYSSEUS_EDIT_MAX_BODY_MB", "").strip()
    if not raw:
        return DEFAULT_MAX_BODY_BYTES
    try:
        return max(1, int(raw)) * 1024 * 1024
    except ValueError:
        raise ConfigError(f"ODYSSEUS_EDIT_MAX_BODY_MB must be an integer, got {raw!r}") from None


def _allowed_hosts(settings: Settings) -> list[str]:
    hosts = list(LOOPBACK_HOSTS)
    if settings.host not in ("0.0.0.0", "::", ""):
        hosts.append(settings.host)
    extra = os.environ.get("ODYSSEUS_EDIT_ALLOWED_HOSTS", "")
    hosts.extend(h.strip() for h in extra.split(",") if h.strip())
    return hosts


def create_app(
    settings: Settings | None = None,
    *,
    web_dir: Path | str | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    max_body_bytes: int | None = None,
    allowed_hosts: Iterable[str] | None = None,
) -> FastAPI:
    if settings is None:
        settings = load_settings()

    app = FastAPI(title="Odysseus-Edit", version=__version__, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.http_transport = http_transport

    include_routers(app)

    # Unknown /api/* paths get a JSON 404 instead of falling through to static.
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   include_in_schema=False)
    async def api_not_found(path: str) -> Any:
        raise HTTPException(404, f"Unknown API route: /api/{path}")

    web_path = Path(web_dir) if web_dir is not None else _default_web_dir()
    if web_path.is_dir():
        app.mount("/", NoCacheStaticFiles(directory=web_path, html=True), name="web")
    else:
        logger.warning("Frontend directory %s not found; serving the API only", web_path)

        @app.get("/", include_in_schema=False)
        async def no_frontend() -> JSONResponse:
            return JSONResponse({"detail": f"Frontend not found at {web_path}"}, status_code=404)

    # Added last = outermost: reject bad hosts/origins before reading bodies.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_body_bytes or _max_body_bytes())
    app.add_middleware(LocalOriginMiddleware,
                       allowed_hosts=list(allowed_hosts) if allowed_hosts is not None else _allowed_hosts(settings))
    return app


def __getattr__(name: str) -> Any:
    # Module-level `app` for `uvicorn app.main:app`, built on first access so
    # importing this module (tests, tooling) does not read config or .env.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)


def run() -> None:
    """Console script: `odysseus-edit`. Host/port from ODYSSEUS_EDIT_HOST/PORT."""
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"odysseus-edit: {exc}") from None
    enabled = ", ".join(p.id for p in settings.enabled()) or "none"
    logger.info("Config %s; enabled providers: %s", settings.source, enabled)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
