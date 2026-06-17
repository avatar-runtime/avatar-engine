# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""FastAPI application factory: lifespan (DB), single-key auth, routes, dashboard."""

from __future__ import annotations

import contextlib
import hmac
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from avatar.api.ratelimit import TokenBucket
from avatar.config import Settings, check_startup_safety, load_settings
from avatar.engine.db import create_engine, create_session_factory, init_db

_DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    # Fail fast on an insecure production configuration (default API key, etc.).
    check_startup_safety(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(settings.database_url, settings)
        if settings.is_sqlite:
            await init_db(engine)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.settings = settings
        app.state.rate_limiter = TokenBucket(
            settings.rate_limit_per_second, settings.rate_limit_burst
        )
        # Load developer agents/tools if configured (harmless if none).
        with contextlib.suppress(Exception):
            from avatar.engine.registry import load_app

            load_app()
        yield
        await engine.dispose()

    app = FastAPI(title="Avatar", version="0.1.0", lifespan=lifespan)

    from avatar.api.routes import router

    app.include_router(router)

    # Marketing landing at `/` (static), the developer dashboard at `/app`.
    if _DASHBOARD_DIR.exists():
        @app.get("/", response_class=HTMLResponse)
        async def landing() -> str:
            landing_file = _DASHBOARD_DIR / "landing.html"
            if landing_file.exists():
                return landing_file.read_text()
            return _dashboard_html(settings)

        @app.get("/app", response_class=HTMLResponse)
        async def dashboard_index() -> str:
            return _dashboard_html(settings)

        app.mount(
            "/static", StaticFiles(directory=str(_DASHBOARD_DIR)), name="static"
        )

    return app


def _dashboard_html(settings: Settings) -> str:
    """Render the dashboard. The static API key is injected ONLY in dev mode;
    in production the page ships with no key and prompts the operator for one
    (kept in localStorage), so the key is never embedded in served HTML."""
    html = (_DASHBOARD_DIR / "index.html").read_text()
    injected = settings.api_key if settings.dev_mode else ""
    return html.replace("__AVATAR_API_KEY__", injected)


# --- shared dependencies -----------------------------------------------------


async def require_auth(request: Request, authorization: str = Header(default="")) -> None:
    """Single static API key. ``Authorization: Bearer <key>``. Nothing else.

    Uses a constant-time comparison to avoid leaking the key via timing.
    """
    settings: Settings = request.app.state.settings
    expected = f"Bearer {settings.api_key}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


async def get_session(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


# Re-export for routes module convenience.
__all__ = ["create_app", "require_auth", "get_session"]
