# Phase 4 — Dependency License Review

**Date:** 2026-06-17
**Project license:** Apache-2.0.
**Verdict:** compatible. No GPL/AGPL copyleft in the runtime closure.

## Runtime dependencies

| Package | License | Compatible with Apache-2.0? |
|---|---|---|
| fastapi | MIT | ✅ |
| starlette (via fastapi) | BSD-3-Clause | ✅ |
| uvicorn[standard] | BSD-3-Clause | ✅ |
| sqlalchemy[asyncio] | MIT | ✅ |
| pydantic | MIT | ✅ |
| httpx | BSD-3-Clause | ✅ |
| aiosqlite | MIT | ✅ |

## Optional dependencies

| Package | Extra | License | Notes |
|---|---|---|---|
| asyncpg | `postgres` | Apache-2.0 | ✅ Preferred async Postgres driver. |
| psycopg[binary] | `postgres` | **LGPL-3.0** | ⚠️ Used **unmodified** as a separate, pip-installed dependency by the demo's sync side-effect store. LGPL permits this (no relicensing of our Apache code; we neither modify nor vendor it). **Flagged** for awareness. Mitigation if desired: make the demo's Postgres path use asyncpg, dropping psycopg. |

## Dev dependencies

| Package | License |
|---|---|
| pytest | MIT |
| pytest-asyncio | Apache-2.0 |
| anyio | MIT |
| ruff | MIT |

## Notes

- No dependency requires source disclosure of Avatar Engine.
- Apache-2.0 includes an explicit patent grant; all listed permissive licenses
  are one-way compatible into Apache-2.0 distributions.
- Re-run on each dependency bump (e.g. `pip-licenses` in CI) before a release.
