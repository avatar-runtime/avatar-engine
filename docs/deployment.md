# Deployment

| | |
|---|---|
| **Date** | 2026-06-17 |
| **Status** | Production guide for the self-hosted single-tenant engine |

Avatar's only infrastructure dependency is **Postgres**. A production deployment
is: managed Postgres + ≥1 API replica + ≥1 worker, behind a TLS reverse proxy.
`docker-compose.prod.yml` is a complete, opinionated starting point.

> Read [SECURITY.md](../SECURITY.md) first. The two non-negotiables: a strong
> `AVATAR_API_KEY`, and never exposing the API or Postgres ports directly.

## One-command production stack (Caddy + TLS)

```bash
cp .env.prod.example .env
# Edit .env and set, at minimum:
#   AVATAR_API_KEY        openssl rand -hex 32
#   POSTGRES_PASSWORD     a strong password
#   AVATAR_DOMAIN         the public hostname (Caddy gets a TLS cert for it)
#   CADDY_BASICAUTH_HASH  docker run --rm caddy:2-alpine caddy hash-password -plaintext '<pw>'

docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml up -d --scale worker=3   # scale workers
```

What this gives you:

- **Caddy** terminates TLS for `AVATAR_DOMAIN` (automatic Let's Encrypt) and is the
  only thing bound to host ports (80/443).
- **API and Postgres are not published** to the host — only the compose network
  reaches them.
- `/app` (dashboard) and `/metrics` sit **behind HTTP basic-auth**; the `/v1` API is
  protected by the `AVATAR_API_KEY` bearer token.
- `migrate` applies the reviewed `schema.sql` once (idempotent); `api` and `worker`
  **refuse to boot** without a strong key (not in dev mode).

## Schema & migrations

- `avatar migrate` creates the schema **from the ORM models** (`create_all`),
  idempotently (a no-op once the `runs` table exists). Because it derives from the
  models, the live schema always matches what the engine expects — there is no
  hand-written DDL to drift.
- [`avatar/engine/schema.sql`](../avatar/engine/schema.sql) is the **reviewed
  documentation** of that shape (portable `varchar`/`json`/`text` types, identical on
  Postgres and SQLite). `tests/test_schema_drift.py` pins it to the models (table +
  column names, and "no native ENUMs"), so the doc cannot silently diverge.
- **v0.1 has a single baseline schema.** There is no multi-version migration tool
  yet. Before the first schema change in a release with real user data, introduce
  Alembic (baseline = the current models) and ship versioned migrations. Until then,
  treat the schema as the frozen v1 contract.

## Backups & disaster recovery — required

The ledger (`run_steps`) is the **entire system of record**; lose it and you lose
run state *and* the idempotency guarantees (in-flight runs could re-execute tools).
Do not run production without a backup story.

- **Preferred: managed Postgres with PITR** (point-in-time recovery) — RDS,
  Cloud SQL, Neon, Supabase, etc. Enable automated backups + a retention window.
- **Self-managed minimum: a `pg_dump` cron**, shipped off-box:
  ```bash
  pg_dump "$AVATAR_DATABASE_URL" | gzip > avatar-$(date +%F-%H%M).sql.gz
  ```
- **Test a restore** before you rely on it. An untested backup is not a backup.
- The `pgdata` Docker volume is **not** a backup — a `docker compose down -v` deletes
  it permanently.

## Scaling

- **Workers** are stateless — scale by running more (`--scale worker=N`). Throughput
  is bounded by Postgres, not by a broker. Each worker uses a small connection pool
  (`AVATAR_DB_POOL_SIZE`).
- **API** replicas are stateless too; put them behind the proxy. Note the rate
  limiter is **per-process** (in-memory) — with N replicas the effective limit is
  N × `AVATAR_RATE_LIMIT_PER_SECOND`. For a global limit, set it per replica or add a
  gateway.
- **Connection budget:** total Postgres connections ≈ (API replicas × pool) +
  (workers × pool). Keep it under your server's `max_connections`; tune
  `AVATAR_DB_POOL_SIZE` / `AVATAR_DB_MAX_OVERFLOW` accordingly.

## Observability

- **`GET /metrics`** — Prometheus text: runs by status, queue depth, running, dead
  count, oldest-queued age. Scrape it (behind your network policy).
- **`GET /v1/stats`** — the same snapshot as JSON (bearer-auth).
- **`GET /healthz`** (liveness) and **`GET /readyz`** (DB reachable) for your
  orchestrator's probes.
- **Alert** on: `avatar_runs_dead > 0` (poison runs need a human),
  `avatar_oldest_queued_age_seconds` high (workers not keeping up / stalled), and
  `/readyz` failing (DB).

## Operational tuning (env)

| Var | Default | Notes |
|---|---|---|
| `AVATAR_API_KEY` | — | **Required** in prod. `openssl rand -hex 32`. |
| `AVATAR_DEV_MODE` | `0` | `1` only for local dev; relaxes the key guard. |
| `AVATAR_DATABASE_URL` | sqlite (dev) | `postgresql+asyncpg://…` in prod. |
| `AVATAR_LEASE_SECONDS` | `30` | Crash-detection window; lower = faster resume, more heartbeat load. |
| `AVATAR_MAX_ATTEMPTS` | `5` | Re-leases before a run is dead-lettered. |
| `AVATAR_RATE_LIMIT_PER_SECOND` / `_BURST` | `50` / `100` | Per-process enqueue throttle. |
| `AVATAR_MAX_QUEUE_DEPTH` | `10000` | Enqueue returns 429 above this. |
| `AVATAR_DB_POOL_SIZE` / `_MAX_OVERFLOW` | `10` / `20` | Per-process connection pool. |
| `AVATAR_TOOL_ISOLATION` | `inproc` | `subprocess` for less-trusted tools (see SECURITY.md). |
