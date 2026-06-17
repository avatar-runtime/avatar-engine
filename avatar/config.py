# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Process configuration — env vars only, no SaaS settings layer.

The whole point of the wedge: one infra dependency (Postgres) and one auth
secret (a single static API key). Everything else has a sane default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Keys that must never reach a non-local deployment.
INSECURE_API_KEYS = frozenset({"", "dev-key", "changeme", "change-me", "test"})


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    # Dev mode relaxes the production safety checks (allows the default API key,
    # injects the key into the dashboard for convenience). NEVER set in prod.
    dev_mode: bool = False
    # Lease duration; a worker that doesn't heartbeat within this window is
    # considered crashed and its run is re-leased.
    lease_seconds: int = 30
    # Heartbeat cadence inside the run loop.
    heartbeat_seconds: int = 10
    # Worker poll interval when the queue is empty.
    poll_interval_ms: int = 500
    # Safety cap on plan iterations per run (runaway guard).
    max_steps: int = 50
    # Re-leases before a poison run is moved to `dead`.
    max_attempts: int = 5
    # Per-tool subprocess wall-clock timeout (seconds) and response cap (bytes).
    tool_timeout_seconds: int = 30
    tool_max_output_bytes: int = 1_000_000
    # --- backpressure / rate limiting (control API) ---
    # Token-bucket limit on writes (enqueue). 0 disables.
    rate_limit_per_second: float = 50.0
    rate_limit_burst: int = 100
    # Reject enqueue with 429 when this many runs are already queued. 0 = no cap.
    max_queue_depth: int = 10_000
    # --- database connection pool (ignored for SQLite) ---
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


def load_settings() -> Settings:
    return Settings(
        database_url=os.getenv(
            "AVATAR_DATABASE_URL", "sqlite+aiosqlite:///./avatar.db"
        ),
        api_key=os.getenv("AVATAR_API_KEY", "dev-key"),
        dev_mode=_bool("AVATAR_DEV_MODE", False),
        lease_seconds=_int("AVATAR_LEASE_SECONDS", 30),
        heartbeat_seconds=_int("AVATAR_HEARTBEAT_SECONDS", 10),
        poll_interval_ms=_int("AVATAR_POLL_INTERVAL_MS", 500),
        max_steps=_int("AVATAR_MAX_STEPS", 50),
        max_attempts=_int("AVATAR_MAX_ATTEMPTS", 5),
        tool_timeout_seconds=_int("AVATAR_TOOL_TIMEOUT_SECONDS", 30),
        tool_max_output_bytes=_int("AVATAR_TOOL_MAX_OUTPUT_BYTES", 1_000_000),
        rate_limit_per_second=_float("AVATAR_RATE_LIMIT_PER_SECOND", 50.0),
        rate_limit_burst=_int("AVATAR_RATE_LIMIT_BURST", 100),
        max_queue_depth=_int("AVATAR_MAX_QUEUE_DEPTH", 10_000),
        db_pool_size=_int("AVATAR_DB_POOL_SIZE", 10),
        db_max_overflow=_int("AVATAR_DB_MAX_OVERFLOW", 20),
        db_pool_timeout=_int("AVATAR_DB_POOL_TIMEOUT", 30),
    )


class InsecureConfigError(RuntimeError):
    """Raised when a production process is configured with an insecure secret."""


def check_startup_safety(settings: Settings) -> None:
    """Fail fast if a non-dev deployment is configured insecurely.

    The default/empty API key is allowed only when ``AVATAR_DEV_MODE=1`` (the
    local docker-compose and tests set it). Any other deployment that boots with
    ``dev-key`` would be wide open, so we refuse to start.
    """
    if settings.dev_mode:
        return
    if settings.api_key in INSECURE_API_KEYS:
        raise InsecureConfigError(
            "Refusing to start: AVATAR_API_KEY is unset or a known default "
            f"({settings.api_key!r}). Set a strong, unique AVATAR_API_KEY "
            "(e.g. `openssl rand -hex 32`), or set AVATAR_DEV_MODE=1 for local "
            "development only. See SECURITY.md."
        )
