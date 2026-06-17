# Avatar — The Complete Guide

> **A Postgres-native durable execution runtime for AI agent workflows.**
> Crash-safe, replayable, idempotent execution for an LLM agent's
> `plan → tool → observe` loop — with no broker, no scheduler, and no
> orchestrator. Just Postgres.
>postgres was deliberatly chosen as it gives three of the hardest things for free: durable state(WAL, durability,Crash recovery), Concurrency control, 

| | |
|---|---|
| **Status** | v0.1 — MVP shipped, single-tenant, self-hosted |
| **Audience** | Everyone: founders, investors, and engineers. Non-technical sections are marked 🟢; deep-technical sections 🔵. |
| **Read time** | ~30 minutes end-to-end; or jump via the table of contents |
| **One-liner** | Avatar turns tool-calling agent loops into crash-safe, replayable workflows with deterministic recovery and idempotent side effects. |

---

## Table of contents

1. [Executive summary (🟢 non-technical)](#1-executive-summary)
2. [The problem Avatar solves (🟢)](#2-the-problem-avatar-solves)
3. [The core idea & mental model (🟢/🔵)](#3-the-core-idea--mental-model)
4. [Architecture (🔵)](#4-architecture)
5. [The data model — the two tables that *are* Avatar (🔵)](#5-the-data-model)
6. [Execution semantics — the durable loop (🔵)](#6-execution-semantics)
7. [The concurrency model, stated as theorems (🔵)](#7-the-concurrency-model-stated-as-theorems)
8. [Crash safety & the failure taxonomy (🔵)](#8-crash-safety--the-failure-taxonomy)
9. [Idempotency & the exactly-once boundary (🔵)](#9-idempotency--the-exactly-once-boundary)
10. [Replay & time-travel, defined precisely (🔵)](#10-replay--time-travel-defined-precisely)
11. [Policy, budget & human-in-the-loop (🔵)](#11-policy-budget--human-in-the-loop)
12. [**The Guarantees Spec** — what Avatar does and does not guarantee (🟢/🔵)](#12-the-guarantees-spec)
13. [The developer experience — SDK contract (🔵)](#13-the-developer-experience--sdk-contract)
14. [Control API reference (🔵)](#14-control-api-reference)
15. [The dashboard (🟢)](#15-the-dashboard)
16. [Security model (🔵)](#16-security-model)
17. [Deployment & operations (🔵)](#17-deployment--operations)
18. [The killer demo (🟢)](#18-the-killer-demo)
19. [Testing & chaos strategy (🔵)](#19-testing--chaos-strategy)
20. [Positioning & market (🟢)](#20-positioning--market)
21. [Measuring adoption — telemetry & metrics (🟢/🔵)](#21-measuring-adoption--telemetry--metrics)
22. [What is done — v1 status (🟢)](#22-what-is-done--v1-status)
23. [What remains — the way forward to a hardened v1 (🔵)](#23-what-remains--the-way-forward)
24. [After the MVP — the roadmap (v2, v3) (🟢)](#24-after-the-mvp--the-roadmap)
25. [Risks & how we de-risk them (🟢)](#25-risks--how-we-de-risk-them)
26. [Glossary](#26-glossary)
27. [Appendix: repository map](#27-appendix-repository-map)

---

## 1. Executive summary

🟢 **In one sentence:** Avatar is a Postgres-backed durable execution engine for
AI agents that turns tool-calling loops into crash-safe, replayable workflows
with deterministic recovery and idempotent side effects.

AI agents do real things now — issue refunds, send emails, move money, call APIs.
But the loops that drive them (call the model → run a tool → observe → repeat)
are fragile. If the process running an agent dies halfway through — a deploy, a
crash, an OOM kill, a spot-instance reclaim — you are left with a torn workflow:
Did the refund go out? Did it go out *twice*? Where was it? Nobody knows, because
the state lived in the memory of a process that no longer exists.

Avatar makes that loop **durable**. Every decision and every tool call is
committed to an append-only ledger in Postgres *before* anything irreversible
happens. A worker can die at any instant; another worker picks the run up,
rebuilds its exact state from the ledger, and continues — **without losing a
step and without firing the same side effect twice from Avatar's side.**

The proof is a 30-second demo: an agent issues a refund, the worker is killed
with `SIGKILL` mid-refund, a fresh worker resumes, and **the refund is issued
exactly once.** That single property — *"crashed mid-refund, restarted, the
refund wasn't issued twice"* — is the entire product.

**Why it's defensible:** agent frameworks (LangChain, etc.) give you the loop but
no durability. Durable-execution engines (Temporal, Inngest) give you durability
but no agent/tool/LLM semantics. Avatar is the only thing that is **both
agent-native and crash-safe** — and it needs only Postgres to run.

---

## 2. The problem Avatar solves

🟢 Picture a customer-support agent that, for one ticket, must:

1. look up the order,
2. issue a refund (real money leaves your account),
3. email the customer.

Now the worker process running it dies between steps 2 and 3 — a routine deploy,
say. Three bad things can happen with a naive agent loop:

- **Lost work.** The run vanishes; the customer never gets their email, and
  there's no record of where it stopped.
- **Double side effects.** A retry re-runs step 2 and the customer is refunded
  twice.
- **No accountability.** There is no trustworthy log of what the agent decided,
  when, and why — so you can't debug, audit, or prove what happened.

These aren't exotic edge cases; they're the *default* behaviour of every
in-memory agent loop. As soon as an agent touches anything that matters, this
fragility becomes a blocker to shipping.

Avatar removes the blocker. It is the missing **reliability layer** between "my
agent works on my laptop" and "my agent runs real operations in production."

---

## 3. The core idea & mental model

🟢 **The one architectural claim that matters:**

> **The `runs` table *is* the queue. Postgres is the only system of record.**

There is no Redis, no Kafka, no RabbitMQ, no external scheduler, no orchestrator
process. A "run" is a row. Workers find work by querying that table. State is
saved by appending rows to a second table. That's it.

🔵 This collapses three things people normally run as separate systems into one
durable store:

| Normally a separate system | In Avatar |
|---|---|
| The **queue** (Redis/SQS/Kafka) | `runs` rows in `queued` state |
| The **workflow state store** | the `run_steps` append-only ledger |
| The **execution coordinator** | a Postgres row-lease (`FOR UPDATE SKIP LOCKED`) |

The mental model in five words: **an append-only log you fold.** A run's complete
state at any moment is a *pure function of its ledger* — replay the steps from
the beginning and you reconstruct exactly where it was. Nothing important lives
in process memory, which is precisely why a crash loses nothing.

If you know event sourcing: `run_steps` is the event log, the run is the
aggregate, and `rebuild_state()` is the fold. If you know Temporal: the ledger is
the event history and the worker is the replaying executor — but the history is
plain rows in *your* Postgres, queryable with SQL, not hidden in a proprietary
cluster.

---

## 4. Architecture

🔵

```
   ┌──────────────────┐   POST /v1/runs (enqueue)   ┌─────────────────────────┐
   │  Python SDK / CLI │ ──────────────────────────▶ │        Postgres          │
   │  (developer code) │                             │  runs    (queue + state) │
   │  agent fn + tools │ ◀── status / steps / stream │  run_steps (append-WAL)  │
   └────────┬─────────┘                              │  approvals (HITL)        │
            │  model call + tool fns                 └───────────┬─────────────┘
            │  (the developer's own)                             │ FOR UPDATE SKIP LOCKED
            ▼                                                     │ lease + heartbeat
     ┌───────────────┐                                ┌──────────▼──────────┐
     │  Dashboard SPA │ ── REST + SSE ────────────────│   Worker process     │
     │  (/app)        │                                │  execute_run loop    │
     └───────────────┘                                │  policy → dispatch    │
                                                       │  tool (inproc/subproc)│
                                                       └──────────────────────┘
```

**Components (all in this repo):**

- **Engine** (`avatar/engine/`) — the durable core: models + schema, the
  `execute_run` loop, the lease-based worker, idempotency, tools, policy, budget,
  replay. *This is the product;* everything else is ergonomics around it.
- **Control API** (`avatar/api/`) — a FastAPI app: single-key auth, the `/v1`
  REST surface, the SSE stream, rate limiting, `/metrics` and `/v1/stats`.
- **SDK** (`avatar/sdk/`) — `@agent` / `@tool` decorators (authoring) plus a
  REST/SSE client (`runs.create/get/list/wait/stream/replay/...`).
- **Dashboard** (`avatar/dashboard/`) — a single-page client of the API: runs list, the
  step-ledger timeline with visible crash-resume markers, live SSE, fork/replay.
- **CLI** (`avatar/cli.py`) — `avatar serve | worker | migrate | demo`.

**Properties that fall out of the design:**

- **One infra dependency.** Postgres. Operationally boring on purpose.
- **Stateless, horizontally-scalable workers.** More throughput = more worker
  processes. No leader election, no partition assignment.
- **The dashboard and SDK are *just clients*** of the same `/v1` API — no
  privileged backchannel.

---

## 5. The data model

🔵 Avatar is two tables (plus a small human-approval side table). The canonical,
reviewed DDL lives in [`avatar/engine/schema.sql`](../avatar/engine/schema.sql);
a drift test (`tests/test_schema_drift.py`) pins it to the ORM models so the
documentation can never silently diverge from the code.

### `runs` — the durable run record *and* the queue

| Column | Meaning |
|---|---|
| `id` | UUID (app-generated hex). |
| `agent_ref` | Which agent definition to execute. |
| `status` | `queued · leased · running · paused · approval_wait · succeeded · failed · dead`. |
| `input` / `output` | The run's input payload and final output (JSON). |
| `cursor_seq` | Seq of the last committed step. Advances **in the same transaction** as the step insert. |
| `lease_owner` / `lease_expires_at` | The single worker that currently owns the run, and when its lease lapses. `NULL` when unleased. |
| `attempt` | Incremented on every (re)lease — the crash/resume counter. |
| `budget_cap_cents` / `budget_used_cents` | Per-run cost ceiling and accrual. |
| `error_class` | Failure taxonomy: `model · tool · policy · budget · infra · cancelled`. |
| `cancel_requested` | Cooperative cancel flag, honored each step. |
| `idempotency_key` | Caller-supplied; dedups *enqueue* (`UNIQUE`). |
| `forked_from` / `fork_seq` | Replay provenance. |

### `run_steps` — the append-only ledger (the heart of the system)

| Column | Meaning |
|---|---|
| `run_id`, `seq` | The run and its strict, gap-free step ordering. `UNIQUE(run_id, seq)`. |
| `type` | `plan · tool_call · observation · approval_wait · final · error`. |
| `payload` | The step body (plan text + requested tool calls, tool args, observation result, …). |
| `tool_call_id` | The model-assigned id of a tool call — drives idempotency. |
| `idempotency_key` | Derived key for a tool effect. `UNIQUE(run_id, idempotency_key)` — **the exactly-once-record guarantee.** |
| `cost_cents` | Cost attributed to this step (folds into the budget). |
| `worker_id`, `attempt` | Who committed the step and on which attempt — this is what renders the "▸ resumed after crash" markers. |

**The three inviolable rules** (enforced by the engine, asserted by tests):

1. `run_steps` rows are **append-only** — never `UPDATE` a payload, never
   `DELETE` a row.
2. `cursor_seq` advances **in the same transaction** as the step insert (a step
   is either fully committed or not at all).
3. A tool's observation is recorded under `UNIQUE(run_id, idempotency_key)`,
   making it **physically impossible to record a side effect twice.**

### `approvals` — human-in-the-loop decisions

A small side table: one row per `(run_id, tool_call_id)` for a `require_approval`
tool. The ledger stays the source of truth for *execution*; this records the
out-of-band human decision the engine reads when it re-leases a parked run.

---

## 6. Execution semantics

🔵 The engine is a single function, `execute_run`, that advances one run by
committing **one step at a time**. The invariant that makes it safe:

> **Every tool call is preceded by a committed `tool_call` (intent) step, and
> its result is recorded under `UNIQUE(run_id, idempotency_key)`.**

The loop (simplified from `avatar/engine/runtime.py`):

```
execute_run(run):
  state = rebuild_state(load_steps(run.id))     # a pure fold over the ledger
  loop:
    heartbeat(run)                              # renew lease AND confirm we still own it
    if run.cancel_requested: fail(cancelled); return
    if pending tool calls (from the last plan, not yet observed):
        for each call:
            decision = policy(run, call)
            if deny:            commit observation{error: policy_denied}; continue
            if require_approval: commit approval_wait; park run; release lease; return
            key = idempotency_key(run, call)
            if observation_exists(key): continue          # already done (resume short-circuit)
            commit tool_call (INTENT, key)                # committed BEFORE dispatch
            result = dispatch_tool(call, idem_header=key) # in-proc or subprocess
            commit observation(result, key)               # the exactly-once record
        continue
    else:                                       # nothing pending → ask the model
        plan = call_model(state)                # the developer's model function
        if would_exceed_budget(plan.cost): fail(budget); return
        commit plan
        if plan.is_final: commit final; succeed; return
```

Why each piece exists:

- **`rebuild_state` first, always.** A worker never trusts memory; it derives
  everything from the ledger. This is what makes a fresh worker resume a run it
  never started.
- **Intent before dispatch.** Committing the `tool_call` *before* calling the
  tool means that even if the worker dies the instant after dispatch, the ledger
  records that the call was *attempted* — the resuming worker knows not to treat
  it as fresh.
- **Observation under a unique key.** The result is recorded under
  `UNIQUE(run_id, idempotency_key)`, so a second attempt to record the same
  effect is rejected by the database, not by application logic.

---

## 7. The concurrency model, stated as theorems

🔵 This is the part engineers and investors will press on. State it like a
theorem, because it is one.

**Theorem 1 (Single active owner).** *At any instant, a run has at most one
worker permitted to commit steps to it.*

*Mechanism.* A worker claims a run with an atomic lease:

```sql
-- Postgres: contention-free claim of one runnable row
UPDATE runs SET status='leased', lease_owner=:me,
       lease_expires_at = now() + :lease, attempt = attempt + 1
WHERE id = (
  SELECT id FROM runs
  WHERE status='queued'
     OR (status IN ('leased','running') AND lease_expires_at < now())  -- crashed owner
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED        -- concurrent workers grab *different* rows
  LIMIT 1
) RETURNING *;
```

`FOR UPDATE SKIP LOCKED` guarantees two workers scanning simultaneously select
*different* rows (on SQLite, a compare-and-swap `UPDATE … WHERE status=? AND
lease_expires_at=?` whose `rowcount==1` proves exclusive ownership). Ownership is
therefore decided by the database, atomically.

**Theorem 2 (No split-brain writes).** *A worker that has lost its lease cannot
commit a step or a terminal status.*

*Mechanism.* Every heartbeat is a **guarded** update:

```sql
UPDATE runs SET lease_expires_at = now() + :lease
WHERE id=:run AND lease_owner=:me;     -- rowcount==0 ⇒ we were displaced
```

If another worker re-leased the run (because our lease lapsed), `rowcount==0`,
the engine raises `LeaseLostError`, and the displaced worker **stops immediately
without touching state.** A double-commit — the only thing that could finish a
run, or fire a tool, twice — is structurally prevented.

**Theorem 3 (Progress).** *If at least one worker is alive and a run is runnable,
the run eventually makes progress.* A crashed owner's lease expires after
`lease_seconds`; the run becomes re-claimable; the next worker resumes it. A
run that exceeds `max_attempts` re-leases is moved to `dead` (poison-run guard)
so one bad run cannot consume the fleet forever.

---

## 8. Crash safety & the failure taxonomy

🔵 Trust comes from naming every failure mode and showing the response. Avatar's
worker can be killed at four lifecycle points; the decisive one is **CRASH-C.**

| Code | When the worker dies | What's on the ledger | Resume behaviour |
|---|---|---|---|
| **CRASH-A** | After claiming, before any step | nothing new | Re-lease; start the plan fresh. No side effect occurred. |
| **CRASH-B** | After `plan` commit, before tool dispatch | `plan` only | Re-lease; the tool call is *pending* → dispatched normally. No double effect. |
| **CRASH-C** | **After tool dispatch, before `observation` commit** | `plan` + `tool_call` intent, **no** observation | Re-lease; call is pending; **re-dispatched with the same idempotency key** → downstream dedupes → effect happens once. |
| **CRASH-D** | After `observation` commit, before next step | `plan` + `tool_call` + `observation` | Re-lease; the call is already observed → **short-circuited** to the recorded result. No re-dispatch. |

Other failure modes and their handling:

- **Network partition / stalled worker.** The worker can't heartbeat → its lease
  lapses → another worker takes over (Theorem 1). If the original wakes up and
  tries to commit, the guarded heartbeat rejects it (Theorem 2).
- **Postgres lease loss (displaced).** `LeaseLostError` → stop without writing.
- **Tool timeout / tool crash.** Bounded by a wall-clock timeout and (optional)
  subprocess isolation; recorded as an `error` observation with
  `error_class='tool'`, with a configurable per-tool retry count.
- **Poison run.** A run that keeps failing past `max_attempts` → `dead`,
  surfaced on the dashboard and in `/metrics` (`avatar_runs_dead`).

CRASH-C is the one the [killer demo](#18-the-killer-demo) reproduces with a real
`SIGKILL`, and the one the test harness injects deterministically.

---

## 9. Idempotency & the exactly-once boundary

🔵 This is where careless systems overclaim. Avatar is deliberately precise — and
we hold this wording everywhere (in the code, the demo output, and the docs),
because reviewers *will* test it.

> **At-most-once dispatch from Avatar — always. Exactly-once end-to-end — iff
> the tool honors the idempotency key.** We never claim unconditional
> exactly-once.

Or, in the demo's own words:

> *Tool dispatch attempts may repeat, but tool effects cannot duplicate when
> idempotency is enforced.*

**How the key works.** For each tool call the engine derives a **crash-stable**
key — `sha256(run_id : tool_call_id : tool_name : canonical_args)`. It is keyed
on the *model-assigned `tool_call_id`*, never a mutating counter, so a worker
that resumes after a crash recomputes the **same** key. Two layers use it:

- **Layer 1 (record).** `UNIQUE(run_id, idempotency_key)` makes it impossible to
  write two observations for the same call — the ledger is exactly-once *by
  construction*.
- **Layer 2 (dispatch).** The key is passed to the tool (as
  `avatar.current_idempotency_key()`, intended for forwarding as an
  `Idempotency-Key` header). If the worker died in the CRASH-C window and
  re-dispatches, an honoring downstream (Stripe, etc.) dedupes the second call.

**The honest caveat:** a tool with a non-idempotent side effect that *ignores*
the key can still double-apply on a CRASH-C resume. That is a property of the
downstream, not of Avatar — and we say so plainly in
[SECURITY.md](../SECURITY.md).

---

## 10. Replay & time-travel, defined precisely

🔵 "Deterministic replay" is meaningless unless you define it. Avatar's
definition:

> **Replay = reconstructing run state by re-reading the ledger prefix, *without*
> re-calling the model and *without* re-executing tools.** Recorded `plan` steps
> stand in for model calls; recorded `observation` steps stand in for tool
> results.

Two operations:

- **`replay_trace(run)`** — a *pure read*. Walks the ledger and reconstructs the
  decision sequence and output with **zero side effects** (no model tokens spent,
  no tools run). This proves the trace is a faithful, debuggable record — the
  property LangChain-style loops cannot offer.
- **`fork_run(run, from_seq)`** (the dashboard's "Fork here", `POST /replay`) —
  copies steps `0..from_seq` of a run into a brand-new run and resumes *forward*
  from that point. The copied prefix is reused (its plans/observations are read,
  not re-executed), so prior tool side effects are **not** repeated; only steps
  *after* the fork point run freshly. Because idempotency keys embed the run id,
  they are recomputed for the new run on copy — which is what lets the resuming
  worker short-circuit the copied calls instead of re-dispatching them.

Use cases: debugging ("what would the agent do if I changed the tool's output at
step 7?"), recovering a stuck run, and A/B-ing a prompt change from a fixed
historical point.

---

## 11. Policy, budget & human-in-the-loop

🔵

- **Policy hook.** A synchronous callback evaluated *before every tool dispatch*,
  returning `allow | deny | require_approval`. Default: allow-all. `deny` records
  an `error` observation and the agent continues (it sees the denial);
  `require_approval` parks the run.
- **Human-in-the-loop (HITL).** On `require_approval`, the engine commits an
  `approval_wait` step, sets the run to `approval_wait`, and **releases the
  lease** (no worker is tied up waiting). A human resolves it via
  `POST /approve` / `/reject` (or the dashboard buttons); the run is re-queued
  and the next worker either dispatches or records the rejection.
- **Budget.** A per-run `budget_cap_cents`. Each step accrues `cost_cents`; the
  engine **hard-stops before the next step** once the cap would be exceeded,
  failing with `error_class='budget'`. *Caveat:* a call already in flight when
  the cap is hit is not cancelled — treat the cap as a circuit breaker, not a
  pre-charge.

---

## 12. The Guarantees Spec

🟢/🔵 *(This one page is the investor/engineer trust weapon. Print it.)*

### Avatar **guarantees**

- ✅ **Durability of state.** Every committed step is in Postgres before the next
  action. A worker crash loses **zero** committed steps.
- ✅ **Single-owner execution.** At most one worker may commit to a run at a time
  (lease + guarded heartbeat). No split-brain writes.
- ✅ **At-most-once tool dispatch from Avatar.** Avatar will never *initiate* the
  same tool call twice from its own logic outside the documented crash-window
  re-dispatch (which carries the same idempotency key).
- ✅ **Exactly-once *record*.** It is impossible to record two observations for
  one tool call (`UNIQUE(run_id, idempotency_key)`).
- ✅ **Deterministic replay.** The ledger reconstructs run state with no model
  calls and no tool execution.
- ✅ **Crash-resume.** A killed run is re-leased and continues from its
  `cursor_seq`, visibly (attempt markers).
- ✅ **Poison-run containment.** A run failing past `max_attempts` is
  dead-lettered, not retried forever.

### Avatar does **not** guarantee (today, by design)

- ❌ **Unconditional exactly-once side effects.** End-to-end exactly-once holds
  *only if* the tool/downstream honors the idempotency key.
- ❌ **Multi-tenancy / per-user authz.** One static API key; full control to
  whoever holds it. (v2.)
- ❌ **A network/filesystem sandbox for tools.** No SSRF/egress filtering yet;
  run only trusted first-party tool code. (v3.)
- ❌ **Cancellation of an in-flight model/tool call.** Cancel and budget act at
  step boundaries.
- ❌ **Cross-process/global rate limiting.** The limiter is per API process.
- ❌ **Proven scale beyond a single Postgres.** Correct by construction; *scale
  numbers are still being earned* (see [Risks](#25-risks--how-we-de-risk-them)).

---

## 13. The developer experience — SDK contract

🔵 A developer wraps their *existing* tool-calling loop and gets durability for
free. They write two things: a **model function** and **tools**.

```python
from avatar import Avatar, tool, Plan, ToolCall

app = Avatar(api_url="http://localhost:8080", api_key="...")

@tool(timeout=10, retries=2)
def issue_refund(order_id: str, cents: int) -> dict:
    # Your real side effect. Forward the key for end-to-end exactly-once:
    key = avatar.current_idempotency_key()
    return stripe.Refund.create(..., idempotency_key=key)

@app.agent("support-resolver")
def resolve(state):
    # A pure step function: read the rebuilt-from-ledger state, return the next Plan.
    if any(m["role"] == "tool" for m in state.messages):
        return Plan(final=True, output={"status": "refunded"})
    return Plan(tool_calls=[ToolCall(id="c1", name="issue_refund",
                                     arguments={"order_id": "42", "cents": 500})])

# Enqueue and wait (from anywhere):
run = app.runs.create(agent_ref="support-resolver", input={"ticket_id": 42})
print(app.runs.wait(run["id"]))
```

**Design choice worth understanding.** Avatar uses a **model function**
`(State) -> Plan` rather than a free-running generator (`while True: ctx.model();
ctx.call_tool()`). The engine drives the loop; the function just produces the
next decision from the ledger-derived `state`. This is the *naturally crash-safe*
shape: there is no suspended call stack to resurrect, only a pure function of an
append-only log. Past plans are read from the ledger, never re-called.

The worker imports the developer's module via `AVATAR_APP=yourpkg.agents`, which
runs the `@agent`/`@tool` decorators and registers them. Then `avatar worker`
executes runs; the SDK's `app.runs.*` is a thin REST/SSE client of the same API.

---

## 14. Control API reference

🔵 Every `/v1` route requires `Authorization: Bearer $AVATAR_API_KEY` (constant-
time compared). Writes are rate-limited; enqueue also honors a max-queue-depth
backpressure (HTTP 429).

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/runs` | Enqueue `{agent_ref, input, budget_cap_cents?, idempotency_key?}` → `202 {id, status}`. |
| GET | `/v1/runs` | List/filter (`?status=&limit=`). |
| GET | `/v1/runs/{id}` | Status + summary (cost, attempt, error_class, output). |
| GET | `/v1/runs/{id}/steps` | The full append-only ledger. |
| GET | `/v1/runs/{id}/stream` | **SSE** — live step events as they commit. |
| POST | `/v1/runs/{id}/cancel` | Cooperative cancel. |
| POST | `/v1/runs/{id}/approve` · `/reject` | Resolve an `approval_wait`. |
| POST | `/v1/runs/{id}/replay` | Fork from a step: `{from_seq}` → new run reusing the prefix. |
| GET | `/v1/stats` | Fleet snapshot as JSON (by-status, queue depth, dead count, oldest-queued age). |
| GET | `/metrics` | Prometheus text exposition (unauthenticated by convention; gate at the proxy). |
| GET | `/healthz` · `/readyz` | Liveness / readiness (DB reachable). |

---

## 15. The dashboard

🟢 A single-page app served by the API. Four views:

- **Runs** (`/app#/`) — table of id, agent, status badge, attempt, cost, age;
  auto-refreshing, with a pulse on `running` and red/amber badges for
  `failed`/`dead`/`approval_wait`.
- **Run detail** (`#/runs/:id`) — the centerpiece: the append-only **step-ledger
  timeline**, each step expandable to its payload JSON, `tool_call_id`,
  `idempotency_key`, committing `worker_id`, and `attempt`. A **▸ resumed after
  crash (attempt N)** divider makes durability *visible*. Live updates over SSE.
  **Fork here** on every step, plus **Approve / Reject / Cancel**.
- **Demo** (`#/demo`) — a narrated one-screen telling of the crash-resume story.
- **Health** (`#/health`) — totals, queue depth, status breakdown.

In production the dashboard ships with **no key embedded**; the operator enters
it once (kept in the browser's `localStorage`), so serving the page never leaks
the key. (In `AVATAR_DEV_MODE=1` the key is injected for local convenience.)

---

## 16. Security model

🔵 Avatar is **single-tenant infrastructure you self-host**; its trust boundary
is *your* network and *your* Postgres. Full detail in [SECURITY.md](../SECURITY.md).
The essentials:

- **Auth = one static bearer key.** No users/roles/tenants. The app and worker
  **refuse to boot** in non-dev mode if the key is unset or a known default
  (`dev-key`, …). Generate with `openssl rand -hex 32`.
- **Never expose the API/Postgres ports directly.** Front with a TLS reverse
  proxy; `docker-compose.prod.yml` ships Caddy (automatic HTTPS) and publishes
  only 80/443.
- **Gate `/app` and `/metrics`** behind basic-auth at the proxy. `/metrics` is
  unauthenticated at the app layer so a scraper can reach it.
- **Tool code runs in the worker by default.** A misbehaving tool can crash the
  worker; use `AVATAR_TOOL_ISOLATION=subprocess` for less-trusted tools. **There
  is no network sandbox yet — do not run untrusted third-party tools.**
- **The ledger is the whole system of record.** Lose Postgres and you lose run
  state *and* the idempotency guarantees → **backups are mandatory** (PITR or a
  tested `pg_dump` cron).

---

## 17. Deployment & operations

🔵 Full guide: [docs/deployment.md](deployment.md). Highlights:

- **Local / dev:** `pip install -e ".[dev]"`, then `avatar demo`, or `avatar
  serve` + `avatar worker` against the default SQLite file. SQLite is for dev and
  fast tests only.
- **Production:** managed Postgres + ≥1 API replica + ≥1 worker, behind TLS.
  `docker compose -f docker-compose.prod.yml up -d --build`, scale workers with
  `--scale worker=N`.
- **Schema:** `avatar migrate` creates the schema **from the ORM models**
  (`create_all`), idempotently — no hand-written DDL to drift. `schema.sql` is
  the reviewed documentation, pinned by a drift test. Versioned migrations
  (Alembic) come before the first post-v1 schema change.
- **Scaling:** workers are stateless — add processes; throughput is bounded by
  Postgres, not a broker. Watch the **connection budget**: total ≈ (API replicas
  × pool) + (workers × pool); keep under `max_connections`.
- **Observability:** scrape `/metrics`; alert on `avatar_runs_dead > 0` (poison
  runs need a human), a rising `avatar_oldest_queued_age_seconds` (workers behind
  or stalled), and `/readyz` failing (DB).

---

## 18. The killer demo

🟢 `python -m avatar.cli demo` (or the **Demo** dashboard page). It runs
`lookup_order → issue_refund → email_customer`, then **kills the worker process
with `SIGKILL` after the refund dispatches but before its observation commits**
(CRASH-C). A fresh worker re-leases the run and finishes it:

```
---- timeline ----
  #1  [plan]
  #2  [tool_call] c1
  #3  [observation] c1
  #4  [plan]
  #5  [tool_call] c2
   ▸ resumed by host:6226 (attempt 2)     ← worker #1 was killed here
  #6  [observation] c2
  ...
  #11 [final]
------------------
run status        : succeeded
dispatch attempts : 2     (the tool was physically called twice — crash + resume)
tool effects      : 1     (the refund happened exactly once)

✅ "Crashed mid-refund. Restarted. The refund wasn't issued twice."
   Tool dispatch attempts may repeat, but tool effects cannot
   duplicate when idempotency is enforced.
```

This is the pitch in 30 seconds, and it runs in CI on every push.

---

## 19. Testing & chaos strategy

🔵 The suite (22 tests today, green on SQLite and Postgres in CI):

- **Engine / nominal** — a healthy run completes with a gap-free, well-formed
  ledger ending in `final`.
- **Crash-resume (CRASH-C)** — the decisive durability slice: inject a crash
  after dispatch, resume, assert the side effect happened **exactly once**, the
  run succeeds, and the resume is visible (attempt advanced, observation
  committed by the second worker).
- **Idempotency** — keys are crash-stable; no duplicate observation keys.
- **Replay / fork** — a fork reuses the prefix without re-running the tool or
  re-calling the model; `replay_trace` is a pure read.
- **Policy / budget / approval** — deny blocks the side effect; `require_approval`
  parks then resumes; budget hard-stops with `error_class='budget'`.
- **API** — auth (401), enqueue/list/get/steps, idempotent enqueue, 404, health.
- **Hardening** — `test_startup_safety` (refuses insecure key) and
  `test_schema_drift` (DDL pinned to models).

**The control-vs-test method:** for the same agent, run a *control* (healthy) and
a *test* (one injected fault) and assert the test trace differs from the golden
control trace **only** by the expected resume delta — `tool effects == 1`,
budget charged once.

**What's next (chaos at scale):** run the crash/race tests against real Postgres
with `kill -9` on live worker processes, simulated network delay, and hundreds of
concurrent runs — the credibility anchor described in
[Risks](#25-risks--how-we-de-risk-them).

---

## 20. Positioning & market

🟢 **Long-term framing (category-defining, not derivative):**

> **A Postgres-native durable execution runtime for AI agent workflows.**

The shorthand *"Temporal for AI agents"* / *"Stripe-level reliability for AI tool
execution"* is useful for instant recognition, but the durable framing above is
the one to lead with — it highlights the real innovation (Postgres-first
execution) instead of anchoring on a competitor.

**The seam nobody else occupies:**

| | Agent loop semantics | Durable execution |
|---|---|---|
| LangChain / agent frameworks | ✅ | ❌ |
| Temporal / Inngest / Trigger.dev | ❌ (not AI-native) | ✅ |
| **Avatar** | ✅ | ✅ |

**Three possible trajectories** (in order of strength):

1. **Infrastructure layer** *(strongest)* — "the Postgres-native durable
   execution layer for AI workloads." Competes with Temporal/Inngest/Trigger.dev
   but AI-native.
2. **Agent operating system** — "the runtime every serious AI agent eventually
   runs on." The LangChain-replacement layer.
3. **Cloud platform** *(later)* — hosted runs, observability, usage-based
   billing, enterprise controls.

**What it is *not* (and that's the point):** not a SaaS, not multi-tenant, not a
marketplace, not BYOK, not voice/avatars, not multi-agent orchestration. Those
are sequenced behind the wedge (see [the roadmap](#24-after-the-mvp--the-roadmap)),
not abandoned — they live in the separate, commercial Avatar Cloud repository.

---

## 21. Measuring adoption — telemetry & metrics

🟢/🔵 For an OSS infra tool, "users" is slippery. There are three different
numbers people conflate:

1. **PyPI downloads** (if/when published) — a *proxy* for top-of-funnel, not
   truth. Inflated by CI, caches, bots; not unique users. Track via pepy.tech /
   libraries.io, but never report it as "users."
2. **Active users** — knowable *only* if the SDK/worker phones home. This is how
   serious infra tools measure reality.
3. **Avatar's *real* metrics** — because Avatar is a durable execution engine,
   the numbers that matter are about *execution*, not installs:
   - **Durable runs created** (`agent_run_started`) — real workload.
   - **Active runs / day** — concurrent execution.
   - **Tool executions** — real work vs. toy usage.
   - **Replay events** — resilience usage.
   - **Crash-recovery events ("resumed after crash")** — *the killer metric*; it
     means the core promise is being exercised in the wild.

**Funnel:** SDK installs = top-of-funnel → API calls = real users → **durable
runs = real product usage.**

**Telemetry design (the decision to make consciously):**

- **Option A — no telemetry** (privacy-first OSS): only PyPI stats; adoption is
  invisible.
- **Option B — opt-in telemetry** *(recommended for OSS)*: off by default for
  OSS, on for cloud; minimal, anonymized.
- **Option C — full SaaS reporting**: the cloud control plane sees usage
  natively (enables dashboards, usage-based billing, enterprise visibility).

If implemented, keep it **async, fire-and-forget, never blocking execution**, and
send the *minimum*: a hashed machine id + event name + version. Suggested
event schema:

```json
{ "event": "agent_run_started", "version": "0.1.x", "machine_id": "sha256(...)", "ts": 0 }
{ "event": "crash_resume",      "version": "0.1.x", "machine_id": "sha256(...)", "ts": 0 }
```

**Do not rely on** GitHub stars, Docker pulls, or raw PyPI counts as success
metrics — they are vanity/partial signals. The honest success metric for v1 is
*"agents actually executing durable workflows on the runtime."*

---

## 22. What is done — v1 status

🟢 Shipped and green in CI:

- ✅ Durable `execute_run` loop with intent-before-dispatch.
- ✅ Append-only `run_steps` ledger as the sole source of truth.
- ✅ Lease-based worker: `FOR UPDATE SKIP LOCKED` (Postgres) + portable CAS
  (SQLite), heartbeat with ownership guard, crash-resume, poison-run dead-letter.
- ✅ Crash-stable idempotency; exactly-once *record* via the unique index.
- ✅ Policy hook (allow/deny/require_approval), per-run budget, HITL approvals.
- ✅ Deterministic replay + fork-from-step.
- ✅ Control API (single-key auth, SSE), rate limiting + queue backpressure.
- ✅ Python SDK (`@agent`/`@tool` + REST/SSE client).
- ✅ Dashboard (runs, timeline with crash-resume markers, live SSE, fork,
  approve/reject) + marketing landing page.
- ✅ Production hardening: startup-safety key check, constant-time auth,
  connection pooling, `/metrics` + `/v1/stats`, `avatar migrate`, schema-drift
  test, Caddy/TLS prod compose, `SECURITY.md` + `docs/deployment.md`.
- ✅ The killer demo (real `SIGKILL`) + 22-test suite on SQLite **and** Postgres.

---

## 23. What remains — the way forward

🔵 To go from "correct MVP" to "infra people bet their business on," in priority
order:

1. **Freeze the API/SDK surface.** Declare v0.1 the contract; no more shape
   changes. Everything below builds *on* it.
2. **Chaos test suite at scale** *(highest-leverage credibility work).*
   - `kill -9` live worker processes mid-transaction, repeatedly, under load.
   - Simulated network latency/partition between worker and Postgres.
   - 1,000+ concurrent runs; assert **zero** duplicate tool effects and zero lost
     steps across the whole fleet.
   - Property/fuzz tests over interleavings (e.g. with a deterministic scheduler).
3. **Write the one-page Guarantees Spec as a standalone artifact** (lift
   [§12](#12-the-guarantees-spec)) — the YC/investor/engineer trust weapon.
4. **Alembic baseline + versioned migrations** before the first post-v1 schema
   change with real user data.
5. **Tool sandboxing** (the biggest security gap): egress allowlist,
   metadata-IP/SSRF blocking, optional microVM/gVisor — so untrusted tools become
   safe to run.
6. **Opt-in telemetry** ([§21](#21-measuring-adoption--telemetry--metrics)) so
   adoption and the crash-recovery metric are actually visible.
7. **Distribution polish:** publish to PyPI (`pip install avatar-engine`), a
   <10-minute "build your first durable agent" quickstart, a TypeScript SDK
   mirroring the Python surface, and 1–2 real integrations (Stripe test-mode,
   an email provider) wired into examples.
8. **Cross-process rate limiting** (Redis or DB token bucket) for multi-replica
   API deployments.
9. **Performance baseline:** publish throughput/latency numbers (runs/sec per
   worker, steps/sec, Postgres connection budget) so adopters can size.

---

## 24. After the MVP — the roadmap

🟢 The cut features aren't deleted — they're **sequenced behind the wedge**, each
built *on top of* something developers already run (the Temporal Cloud / Stripe
dashboard pattern).

### v1 (now) — the OSS engine
Earn adoption and trust with one ownable guarantee: crash-safe, idempotent AI
tool execution on Postgres alone.

### v2 — Avatar Cloud (where revenue lives)
The hosted version. Now the multi-tenant shell returns — but as **monetization on
top of** the trusted engine:
- Hosted runs + managed Postgres; the multi-tenant control plane, auth/orgs/RBAC.
- BYOK secret vault; usage-based / per-execution billing.
- Hosted observability and longer ledger retention.

### v3 — the hard differentiators (only valuable once durability is trusted)
- **Multi-agent fabric:** delegation/handoff/workflow DAGs *on durable runs*.
- **Enterprise governance & policy at scale:** approvals, DLP, audit (WORM),
  SSO/SCIM.
- **Security kernel:** the full tool sandbox (microVM/gVisor, signed mesh, secret
  broker).
- **Observability/SIEM:** stream the ledger to enterprise security tooling.

The discipline *is* the strategy: hold the cut list. The failure mode is letting
v2 features leak into v1 because they're "almost done" in the Cloud repo.

---

## 25. Risks & how we de-risk them

🟢 **The biggest risk is not design — it is proof at scale.**

> A Postgres-as-queue/orchestrator must demonstrate it holds up beyond a clever
> prototype.

The early success metric is therefore **not features** — it is:

- **1,000+ concurrent runs** sustained on a single Postgres,
- **crash-injection stability** (kill workers relentlessly; no torn runs),
- **zero duplicate tool effects under chaos**,
- and a **published throughput/latency baseline** so adopters can size honestly.

Other risks and mitigations:

| Risk | Mitigation |
|---|---|
| Postgres becomes the bottleneck under fan-out | Connection-pool bounds shipped; publish numbers; document the ceiling and when to shard. The lease stays the correctness authority even if a dispatch accelerator is added later. |
| Overclaiming exactly-once | Disciplined wording everywhere ([§9](#9-idempotency--the-exactly-once-boundary), [§12](#12-the-guarantees-spec)); it's even in the demo output. |
| Untrusted tool code | Documented clearly in SECURITY.md as out-of-scope for v1; sandbox is the v3 security kernel. |
| Scope creep back toward a platform | A hard cut list (Cloud features live in a separate repo); contributions must strengthen the wedge, not broaden it. |

---

## 26. Glossary

- **Run** — one durable agent execution; a row in `runs`. Also the unit of work
  in the queue.
- **Step** — one immutable entry in a run's ledger (`run_steps`): `plan`,
  `tool_call`, `observation`, `approval_wait`, `final`, or `error`.
- **Ledger** — the append-only `run_steps` table; the sole source of truth.
- **Lease** — temporary, single-owner claim on a run, renewed by heartbeats and
  expiring on crash.
- **`cursor_seq`** — the seq of the last committed step; the resume point.
- **Idempotency key** — crash-stable key per tool call enforcing the exactly-once
  *record* and enabling exactly-once downstream.
- **Replay** — reconstructing state from the ledger without model calls or tool
  execution.
- **Fork** — a new run that reuses a prefix of an existing run and resumes
  forward.
- **CRASH-C** — the decisive crash window: after dispatch, before the observation
  commits.
- **Poison/dead run** — a run that failed past `max_attempts`; dead-lettered.

---

## 27. Appendix: repository map

```
avatar/
  engine/
    models.py       # runs, run_steps, approvals (+ enums)
    schema.sql      # reviewed canonical DDL (pinned to models by a drift test)
    db.py           # engine/session factory, init_db, migrate, pooling
    runtime.py      # execute_run, rebuild_state, commit_step (the loop)
    worker.py       # claim_next_run (lease), heartbeat, tick_once, run_forever
    idempotency.py  # crash-stable key derivation
    tools.py        # in-proc + subprocess dispatch, timeout/size caps, current_idempotency_key()
    policy.py       # allow/deny/require_approval hook (default allow-all)
    budget.py       # per-run cost accrual + hard-stop
    replay.py       # replay_trace (pure read) + fork_run
    registry.py     # @agent/@tool registry; AVATAR_APP loader; Plan/ToolCall/State
  api/
    app.py          # FastAPI factory, lifespan, single-key auth, landing + /app
    routes.py       # /v1 endpoints, SSE, /v1/stats, /metrics
    ratelimit.py    # in-process token bucket
  sdk/__init__.py   # Avatar client + @agent/@tool + current_idempotency_key
  demo.py           # the killer-demo agent + idempotent refund tool
  cli.py            # avatar serve | worker | migrate | demo
  config.py         # env-only Settings + check_startup_safety
  dashboard/        # single-page dashboard (index.html) + landing.html — packaged in the wheel
docs/
  AVATAR.md         # this document
  deployment.md     # production guide
SECURITY.md         # security model & must-dos
CHANGELOG.md
docker-compose.yml          # local: Postgres + API + dashboard + worker
docker-compose.prod.yml     # production: + Caddy TLS, no published API/DB ports
Caddyfile
tests/              # engine, crash-resume, idempotency, replay, policy/budget, API, hardening
```

---

*Avatar v0.1 — built to prove one guarantee and earn developer trust: a worker
can die at any point and the refund is never issued twice. Everything else is
sequenced behind that.*
