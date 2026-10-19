"""API routers. Add new routers to `ROUTERS`; `include_routers` mounts them
before the static frontend so API paths always win.

To plug in another router, create `app/routers/<name>.py` exposing
`router = APIRouter()` and append it here.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from app.routers import ai, image_ai, image_local, meta

ROUTERS: list[APIRouter] = [
    meta.router,
    image_local.router,
    image_ai.router,
    ai.router,
]


def include_routers(app: FastAPI) -> None:
    for router in ROUTERS:
        app.include_router(router)
