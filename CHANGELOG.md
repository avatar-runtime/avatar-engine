# Changelog

All notable changes to Avatar are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) once published to PyPI.

## [Unreleased]

### Added
- **Production safety guard** — the API and worker refuse to boot in non-dev mode
  with an unset or default `AVATAR_API_KEY` (`avatar.config.check_startup_safety`).
- **`docker-compose.prod.yml` + `Caddyfile`** — TLS reverse proxy (automatic HTTPS),
  basic-auth on `/app` and `/metrics`, no published API/Postgres ports.
- **`avatar migrate`** — idempotently applies the canonical `schema.sql` (Postgres)
  or `create_all` (SQLite).
- **Backpressure** — per-process token-bucket rate limit on enqueue, and a
  `max_queue_depth` cap that returns `429`.
- **DB connection-pool bounds** (`AVATAR_DB_POOL_SIZE` / `_MAX_OVERFLOW` /
  `pool_pre_ping` / `pool_recycle`).
- **`GET /metrics`** (Prometheus) and **`GET /v1/stats`** (JSON): runs by status,
  queue depth, running, dead count, oldest-queued age.
- **Schema-drift test** (`tests/test_schema_drift.py`) asserting `schema.sql` matches
  the ORM models, plus **startup-safety tests**.
- `SECURITY.md` and `docs/deployment.md` (production guide: TLS, backups/PITR,
  scaling, observability, tool-isolation caveats).

### Changed
- The dashboard no longer embeds the API key in production HTML — it prompts the
  operator and stores the key in `localStorage` (the key is injected only in dev
  mode).
- The marketing landing page is served at `/`; the developer dashboard moved to
  `/app`.
- API-key comparison is now constant-time.

### Fixed
- **`schema.sql` was missing the `approvals` table** — a production deploy from the
  canonical DDL would have broken the human-in-the-loop approval flow. Added, and
  now guarded by the drift test.

## [0.1.0] — initial wedge
- Durable execution engine: lease-based worker, append-only step ledger,
  crash-resume, idempotent tool dispatch, deterministic replay/fork.
- Policy hook (`allow`/`deny`/`require_approval`), per-run budget hard-stop.
- Control API (REST + SSE), single-key auth, Python SDK, dashboard.
- The crash-resume "killer demo"; tests on SQLite + Postgres in CI.
