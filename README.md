# Avatar Engine

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/avatar-runtime/avatar-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/avatar-runtime/avatar-engine/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

**Temporal for AI agents.** A Postgres-native durable execution engine for AI
agent workflows — crash-safe, replayable, idempotent. Backed entirely by Postgres.

Wrap your existing tool-calling agent loop and get crash-safety, an append-only
step ledger, idempotent tool dispatch, deterministic replay, and a dashboard to
watch it all — without writing any of that yourself.

```
SDK → API → Postgres → Worker → Tools
                ↑          ↓
           Dashboard ←  SSE stream
```

> **The one guarantee:** a worker can die at any point in a run and another
> resumes from the ledger — no step is lost. **Tool dispatch attempts may repeat,
> but tool effects cannot duplicate when idempotency is enforced.**
>
> Stated precisely: *at-most-once dispatch from Avatar always; exactly-once
> end-to-end iff the tool honors the idempotency key.* We never claim
> unconditional exactly-once.

---

## Why

LangChain-style frameworks give you the agent loop but no durability. Temporal
gives you durability but no agent/tool/LLM semantics. Avatar is the only thing
that is **both agent-native and crash-safe** — and its only infrastructure
dependency is Postgres. The `runs` table *is* the queue. No Redis, no broker.

## Documentation

- **[The Complete Guide](docs/AVATAR.md)** — the comprehensive doc: mental model,
  architecture, execution semantics, the concurrency model as theorems, the
  failure taxonomy, the Guarantees Spec, positioning, adoption metrics, what's
  done, and the roadmap (v1 → v2 Cloud → v3). Start here.
- **[Deployment](docs/deployment.md)** — production guide (TLS, scaling, backups,
  observability).
- **[Security](SECURITY.md)** — the auth model, its limits, and production
  must-dos.

## The 30-second proof: a refund that survives a crash

```bash
git clone https://github.com/avatar-runtime/avatar-engine.git
cd avatar-engine
pip install -e .              # (PyPI: pip install avatar-engine — coming soon)
python -m avatar.cli demo
```

The demo runs `lookup_order → issue_refund → email_customer`. A worker is killed
**after `issue_refund` dispatches but before its observation commits** (the
decisive crash window). A fresh worker re-leases the run, rebuilds from the
ledger, and finishes it:

```
---- timeline ----
  #1  [plan]
  #2  [tool_call] c1
  #3  [observation] c1
  #4  [plan]
  #5  [tool_call] c2
   ▸ resumed by host:6226 (attempt 2)      ← worker crashed here; another took over
  #6  [observation] c2
  ...
  #11 [final]
------------------
run status        : succeeded
dispatch attempts : 2     (the tool was physically called twice — crash + resume)
tool effects      : 1     (one actual refund)

✅ "Crashed mid-refund. Restarted. The refund wasn't issued twice."
   Tool dispatch attempts may repeat, but tool effects cannot duplicate
   when idempotency is enforced.
```

## Quickstart with `docker compose`

```bash
docker compose up           # Postgres + control API + dashboard + 1 worker
# scale workers:  docker compose up --scale worker=3
```

Then open the dashboard at **http://localhost:8088** (the compose host port;
the container serves on 8080), enqueue a run, and watch its live step timeline —
including the visible *"resumed after crash"* marker.

## Use it from Python

```python
from avatar import Avatar, tool, Plan, ToolCall

app = Avatar(api_url="http://localhost:8080", api_key="dev-key")

@tool(timeout=10, retries=2)
def issue_refund(order_id: str, cents: int) -> dict:
    # Your real side effect. Forward avatar.current_idempotency_key() to the
    # downstream service to get exactly-once end-to-end.
    return {"refunded": True}

@app.agent("support-resolver")
def resolve(state):
    # A model function: read the rebuilt state, return the next Plan.
    if any(m["role"] == "tool" for m in state.messages):
        return Plan(final=True, output={"status": "done"})
    return Plan(tool_calls=[ToolCall(id="c1", name="issue_refund",
                                     arguments={"order_id": "42", "cents": 500})])

run = app.runs.create(agent_ref="support-resolver", input={"ticket_id": 42})
print(app.runs.wait(run["id"]))
```

Point the worker at your module with `AVATAR_APP=yourpkg.agents` and run
`avatar worker`. The engine drives the durable loop; you write only the model
call and the tools.

### SDK reference

**Authoring** (imported by the worker via `AVATAR_APP`):

| Symbol | Purpose |
|---|---|
| `@tool(timeout=, retries=, idempotent=)` | Register a developer function as a governed tool. |
| `@app.agent(ref)` / `@agent(ref)` | Register a model function `(State) -> Plan` under `ref`. |
| `Plan(content=, tool_calls=[], final=, output=, cost_cents=)` | The model's output for one step. A plan with no tool calls is final. |
| `ToolCall(id, name, arguments)` | One tool invocation. A **stable `id`** keeps idempotency crash-stable. |
| `State.input` / `State.messages` | The rebuilt-from-ledger view handed to the model each iteration. |
| `current_idempotency_key()` | Inside a tool: the key to forward to your downstream service. |

**Control client** (`app.runs.*`, usable anywhere):

| Call | Maps to |
|---|---|
| `runs.create(agent_ref=, input=, budget_cap_cents=, idempotency_key=)` | `POST /v1/runs` |
| `runs.get(id)` · `runs.list(status=, limit=)` · `runs.steps(id)` | `GET /v1/runs…` |
| `runs.wait(id, timeout=)` | poll until terminal / `approval_wait` |
| `runs.stream(id)` | SSE generator of step events |
| `runs.cancel(id)` · `runs.approve(id)` · `runs.reject(id)` | the POST actions |
| `runs.replay(id, from_seq=)` | `POST /v1/runs/{id}/replay` (fork) |

### Tools, idempotency, and the honest guarantee

A tool receives the idempotency key for the in-flight call via
`current_idempotency_key()`. Forward it to your downstream (e.g. Stripe's
`Idempotency-Key` header). On a crash between dispatch and observation, Avatar
re-dispatches with the **same** key, so:

- **At-most-once dispatch from Avatar** — always, via the committed intent step.
- **Exactly-once end-to-end** — iff your tool/downstream honors the key.

Tools run in-process by default; set `AVATAR_TOOL_ISOLATION=subprocess` to run
each in a child process with the wall-clock timeout and output-size cap enforced.
A crashing in-proc tool can take the worker down, so use `subprocess` for anything
less than fully trusted. **There is no network/SSRF sandbox** (deliberately cut from
the wedge) — do not run untrusted third-party agent code yet. See [SECURITY.md](SECURITY.md).

**Budgets stop runs, not in-flight calls.** `budget_cap_cents` halts a run *before
its next step* once the cap is reached; the model/tool call already in flight is not
cancelled (its provider cost is already incurred). Treat the cap as a circuit
breaker, not a pre-charge.

## How it works

The engine is a state machine over two Postgres tables (`avatar/engine/schema.sql`):

- **`runs`** — the durable run record *and* the work queue. Workers atomically
  lease rows with `FOR UPDATE SKIP LOCKED` (Postgres) or a compare-and-swap
  (SQLite), renew a heartbeated lease, and a guarded update means a worker that
  lost its lease can never commit.
- **`run_steps`** — an append-only, seq-ordered ledger. Steps are never updated
  or deleted; **all run state is a pure fold over this table**, which is what
  makes crash-resume and replay deterministic.

The invariant that makes it safe:

> Every tool call is preceded by a committed `tool_call` (intent) step, and its
> result is recorded under `UNIQUE(run_id, idempotency_key)`.

So on resume an already-observed call short-circuits to its recorded result, and
a call dispatched-but-not-observed (the crash window) is re-dispatched with the
**same** idempotency key — the downstream dedupes it.

The execution loop (`avatar/engine/runtime.py`):

```
rebuild state from the ledger
loop:
  heartbeat (and confirm we still own the lease)
  if pending tool calls:
    for each: policy check → commit intent → dispatch → commit observation
  else:
    call the model → commit plan  (final ⇒ commit final, succeed)
```

## Features

| | |
|---|---|
| **Crash-safe** | Lease + heartbeat + ledger replay. Kill `-9` any worker. |
| **Idempotent tools** | Crash-stable key per tool call; `UNIQUE(run_id, key)`. |
| **Policy hook** | `allow` / `deny` / `require_approval` before every dispatch. |
| **Budget** | Per-run `budget_cap_cents` hard-stop. |
| **Replay / fork** | Re-run a trace prefix without re-calling the model or re-running tools. |
| **Control API** | REST + SSE, single static API-key auth. |
| **Dashboard** | Runs list, step-ledger timeline, live SSE, crash-resume markers, fork. |

## Control API

`Authorization: Bearer $AVATAR_API_KEY` on every `/v1` route.

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/runs` | Enqueue `{agent_ref, input, budget_cap_cents?, idempotency_key?}` |
| GET | `/v1/runs` | List / filter (`?status=&limit=`) |
| GET | `/v1/runs/{id}` | Status + summary |
| GET | `/v1/runs/{id}/steps` | The append-only ledger |
| GET | `/v1/runs/{id}/stream` | SSE — live step events |
| POST | `/v1/runs/{id}/cancel` | Cooperative cancel |
| POST | `/v1/runs/{id}/approve` · `/reject` | Resolve an `approval_wait` |
| POST | `/v1/runs/{id}/replay` | Fork from a step: `{from_seq}` |
| GET | `/healthz` · `/readyz` | Liveness / readiness |

## Dashboard

A single-page client of the API (served at `/`, the static API key injected so
local dev needs no login). Four views:

- **Runs** (`#/`) — table of id, agent, status badge, attempt, cost, age;
  auto-refreshing, with a pulse on `running` and red/amber badges for
  `failed`/`dead`/`approval_wait`.
- **Run detail** (`#/runs/:id`) — the centerpiece. The append-only
  **step-ledger timeline**: each step (`plan / tool_call / observation /
  approval_wait / final / error`) is expandable to its payload JSON, with its
  `tool_call_id`, `idempotency_key`, committing `worker_id`, and `attempt`. A
  **▸ resumed after crash (attempt N)** divider marks where another worker took
  over. Live updates via SSE. **Fork here** on every step, and
  **Approve / Reject / Cancel** when applicable.
- **Demo** (`#/demo`) — a narrated one-screen view of the crash-resume story,
  surfacing any crash-resumed (`attempt ≥ 2`) refund run.
- **Health** (`#/health`) — totals, queue depth, status breakdown.

## Configuration

Everything is an env var — one infra dependency (Postgres), one auth secret.
See [`.env.example`](.env.example).

| Var | Default | Purpose |
|---|---|---|
| `AVATAR_DATABASE_URL` | `sqlite+aiosqlite:///./avatar.db` | Postgres (`postgresql+asyncpg://…`) in production. |
| `AVATAR_API_KEY` | `dev-key` | The single static Bearer key. **Required & strong in prod** (see below). |
| `AVATAR_DEV_MODE` | `0` | `1` for local dev only — relaxes the prod key guard, injects the key into the dashboard. |
| `AVATAR_APP` | — | Module(s) to import so `@agent`/`@tool` register (e.g. `yourpkg.agents`). |
| `AVATAR_LEASE_SECONDS` | `30` | Lease TTL; a worker silent this long is treated as crashed. |
| `AVATAR_HEARTBEAT_SECONDS` | `10` | In-loop lease renewal cadence. |
| `AVATAR_POLL_INTERVAL_MS` | `500` | Worker poll when the queue is empty. |
| `AVATAR_MAX_STEPS` | `50` | Runaway-plan guard per run. |
| `AVATAR_MAX_ATTEMPTS` | `5` | Re-leases before a poison run → `dead`. |
| `AVATAR_TOOL_TIMEOUT_SECONDS` | `30` | Per-tool wall-clock timeout. |
| `AVATAR_TOOL_ISOLATION` | `inproc` | `inproc` or `subprocess` (use `subprocess` for less-trusted tools). |
| `AVATAR_RATE_LIMIT_PER_SECOND` / `_BURST` | `50` / `100` | Per-process enqueue throttle. |
| `AVATAR_MAX_QUEUE_DEPTH` | `10000` | Enqueue returns `429` above this. |
| `AVATAR_DB_POOL_SIZE` / `_MAX_OVERFLOW` | `10` / `20` | Per-process Postgres connection pool. |

> **Production:** the API and worker **refuse to boot** in non-dev mode with an
> unset or default key. Generate one (`openssl rand -hex 32`), and run behind TLS.
> The full guide — Caddy/TLS compose, backups/PITR, scaling, observability,
> tool-isolation caveats — is in **[docs/deployment.md](docs/deployment.md)** and
> **[SECURITY.md](SECURITY.md)**.

## CLI

```bash
avatar serve [--host --port]          # control API + dashboard
avatar worker [--max-runs --max-idle] # a stateless durable worker (scale by running more)
avatar migrate                        # apply the canonical schema (idempotent)
avatar demo                           # the crash-resume killer demo
```

## Project layout

```
avatar/
  engine/        runs+run_steps models, schema.sql, execute_run, worker (lease/
                 heartbeat/resume), idempotency, tools, policy, budget, replay
  api/           FastAPI control API (single-key auth) + SSE
  sdk/           @agent / @tool decorators + REST/SSE client
  demo.py        the killer-demo agent + idempotent refund tool
  cli.py         avatar worker | serve | demo
  config.py      env-only settings
dashboard/       single-page dashboard (served at /)
tests/           engine, crash-resume (CRASH-C), idempotency, replay, policy/budget, API
```

## Development

```bash
pip install -e ".[dev]"
pytest                       # SQLite (fast)
AVATAR_DATABASE_URL=postgresql+asyncpg://avatar:avatar@localhost:5432/avatar pytest
```

The crash/race slice runs against **Postgres** in CI for the true
`FOR UPDATE SKIP LOCKED` path; SQLite covers the rest.

## Scope

This is **single-purpose infrastructure**, not a platform. It is **not** a SaaS,
not multi-tenant, not a marketplace, not BYOK, not voice/avatars, not multi-agent
orchestration. Those belong to **Avatar Cloud** — the hosted, commercial control
plane built on top of this engine — not to the open-source engine. See the
roadmap in [docs/AVATAR.md](docs/AVATAR.md).

## Versioning & releases

Changes are tracked in [CHANGELOG.md](CHANGELOG.md); the project follows
[SemVer](https://semver.org/) and the API is versioned (`/v1`). A PyPI release is
planned (`pip install avatar-engine`); until then install from source
(`pip install -e .`).

## Contributing & governance

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md),
[GOVERNANCE.md](GOVERNANCE.md), and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Avatar Engine is company-led OSS and the open foundation of **Avatar Cloud**, the
hosted multi-tenant control plane built on top of it (the Temporal model).

## License

Licensed under the **Apache License, Version 2.0** — see [LICENSE](LICENSE) and
[NOTICE](NOTICE). Copyright 2026 Avatar Runtime Authors.
