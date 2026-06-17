# Getting started with Avatar

This guide takes you from `pip install` to a durable, crash-safe agent — whether
you're **building a new agent from scratch** or **wrapping an agent loop you
already have**. By the end you'll understand the one contract that makes Avatar
work and have a running agent that survives a mid-run crash exactly once.

New here? Read [the 60-second taste](#the-60-second-taste) and
[the mental model](#the-mental-model-four-sentences) first, then jump to your
path:

- [Path A — build a new agent from scratch](#path-a--build-a-new-agent-from-scratch)
- [Path B — bind Avatar to an existing agent loop](#path-b--bind-avatar-to-an-existing-agent-loop)

For the deep "why it's correct" material — the concurrency theorems, the failure
taxonomy, the Guarantees Spec — see **[The Complete Guide](AVATAR.md)**.

---

## Install

```bash
pip install avatar-runtime
```

For the true Postgres path (production and the real `FOR UPDATE SKIP LOCKED`
concurrency), also install the Postgres extra:

```bash
pip install "avatar-runtime[postgres]"
```

SQLite (bundled, no extra) is fine for local development; Postgres is the system
of record in production. The import package is `avatar`, and the CLI is `avatar`.

---

## The 60-second taste

```bash
pip install avatar-runtime
avatar demo
```

`avatar demo` enqueues a refund run, starts a worker that is **killed
mid-refund** (after the refund tool dispatches but before its result commits),
then starts a fresh worker that resumes from the ledger and finishes the run.
The output proves the headline guarantee:

```
run status        : succeeded
dispatch attempts : 2     (the tool was physically called twice — crash + resume)
tool effects      : 1     (one actual refund)

✅ "Crashed mid-refund. Restarted. The refund wasn't issued twice."
```

That's the whole product in one command. Now let's build your own.

---

## The mental model (four sentences)

1. **A run is a row, and the `runs` table is the queue** — no Redis, no broker;
   workers lease rows with `FOR UPDATE SKIP LOCKED`.
2. **All run state is an append-only ledger** (`run_steps`); a run's current
   state is a pure fold over its steps, which is what makes crash-resume and
   replay deterministic.
3. **You write two things — a model function and your tools** — the engine drives
   the durable `plan → tool → observe → commit` loop around them.
4. **Every tool call commits its intent before dispatch and records its result
   under `UNIQUE(run_id, idempotency_key)`** — so a crash between dispatch and
   observation re-dispatches with the *same* key, and an idempotent tool dedupes
   it. That is the one contract that buys exactly-once.

The two functions you write:

| You write | Signature | Runs in |
|---|---|---|
| **A model function** (`@agent`) | `(State) -> Plan` | the worker, each iteration |
| **Tools** (`@tool`) | your own function | the worker, when the plan calls them |

`State`, `Plan`, and `ToolCall` come from `avatar`. The engine never asks you to
write crash-handling, ledgers, leases, or idempotency yourself.

---

## The three moving parts

Avatar is one package run in three roles — all reading/writing the same database:

```
avatar serve     # the control API + dashboard (enqueue runs, watch them)
avatar worker    # a stateless durable worker (run as many as you want)
your app module  # AVATAR_APP=yourpkg.agents — imported by the worker so your
                 # @agent/@tool decorators register
```

`AVATAR_APP` is how the worker finds your code: set it to an importable module
(or comma-separated modules), and importing it runs your decorators. **The
worker and the API must both be able to import it** (same `PYTHONPATH`).

---

## Path A — build a new agent from scratch

We'll build a tiny support agent that looks up an order and issues a refund —
the same shape as the demo, but yours.

### 1. Write your app module

Create `myagent.py`:

```python
# myagent.py
from avatar import Plan, ToolCall, agent, tool, current_idempotency_key

# --- tools: your real side effects ------------------------------------------

@tool(timeout=10, retries=2)
def lookup_order(order_id: str) -> dict:
    # Read-only; safe to repeat. Return whatever your model needs next.
    return {"order_id": order_id, "amount_cents": 500, "customer": "ada@example.com"}

@tool(timeout=10, retries=2, idempotent=True)
def issue_refund(order_id: str, cents: int) -> dict:
    # The side-effecting tool. Forward the idempotency key to your downstream
    # (Stripe, your billing service, ...) so a re-dispatch in the crash window
    # does NOT issue a second refund.
    key = current_idempotency_key()
    # e.g. stripe.Refund.create(..., idempotency_key=key)
    return {"refunded": True, "order_id": order_id, "cents": cents, "key": key}

# --- the model function: read state, return the next Plan -------------------

@agent("support-resolver")
def resolve(state) -> Plan:
    # state.input is the run's input; state.messages is the ledger-rebuilt
    # conversation. Decide the next step from what's already been observed.
    observed = {m.get("tool_call_id") for m in state.messages if m.get("role") == "tool"}
    order_id = str(state.input["order_id"])

    if "c1" not in observed:
        return Plan(
            content="look up the order",
            tool_calls=[ToolCall(id="c1", name="lookup_order",
                                 arguments={"order_id": order_id})],
            cost_cents=1,
        )
    if "c2" not in observed:
        return Plan(
            content="issue the refund",
            tool_calls=[ToolCall(id="c2", name="issue_refund",
                                 arguments={"order_id": order_id, "cents": 500})],
            cost_cents=1,
        )
    return Plan(final=True, output={"status": "refunded", "order_id": order_id})
```

Key points:

- **`ToolCall.id` must be stable** for a given logical step. Avatar commits the
  plan (ids included) to the ledger, so on resume the pending call is read back
  with its committed id and re-dispatched with the same idempotency key. Stable
  ids are what make idempotency crash-stable. (Here they're hardcoded `c1`/`c2`;
  in Path B you'll get them from your model.)
- A `Plan` with `final=True` (or no `tool_calls`) ends the run with `output`.
- `cost_cents` feeds the per-run budget (see [Budgets](#budgets-policy-and-approvals)).

### 2. Point the engine at it and run

```bash
# One terminal: the control API + dashboard
export AVATAR_APP=myagent
export AVATAR_DEV_MODE=1            # local dev: allows the default dev key
avatar serve                        # http://localhost:8080 (dashboard at /app)

# Another terminal: a durable worker
export AVATAR_APP=myagent
export AVATAR_DEV_MODE=1
avatar worker
```

By default this uses a local SQLite file. To run against Postgres, set
`AVATAR_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db` for
**both** processes and run `avatar migrate` once to create the schema.

### 3. Enqueue a run and watch it

From any Python process (your app, a script, a notebook):

```python
from avatar import Avatar

app = Avatar(api_url="http://localhost:8080", api_key="dev-key")

run = app.runs.create(agent_ref="support-resolver",
                      input={"order_id": "order-42"},
                      budget_cap_cents=1000)
final = app.runs.wait(run["id"])          # polls until terminal / approval_wait
print(final["status"])                     # "succeeded"

for step in app.runs.steps(run["id"]):     # the append-only ledger
    print(step["seq"], step["type"], step.get("tool_call_id"))
```

Or open the dashboard at **http://localhost:8080/app**, enqueue from there, and
watch the live step timeline (including the "▸ resumed after crash" marker when
a worker dies). Scale workers by simply starting more `avatar worker` processes.

That's a complete durable agent. The same code runs unchanged on Postgres with
real `FOR UPDATE SKIP LOCKED` leasing.

---

## Path B — bind Avatar to an existing agent loop

If you already have an agent loop — an LLM call plus some tools — you don't
rewrite it. You **invert it**: instead of *you* running `while: call model →
run tools`, you hand Avatar a model function that produces *one* `Plan` per call
and let the engine run the durable loop. Your tools become `@tool` functions;
your "decide the next step" logic becomes the `@agent` model function.

### The adapter pattern

Avatar's `State.messages` is already an OpenAI/Anthropic-style transcript rebuilt
from the ledger, so the translation is mechanical. Each message is a dict:

```python
{"role": "user", "content": <run.input>}                         # the initial input
{"role": "assistant", "content": "...", "tool_calls": [...]}      # each committed plan
{"role": "tool", "tool_call_id": "c2", "content": "<json result>"}  # each observation
```

Your model function's job for one iteration:

1. Translate `state.messages` into your LLM's message format.
2. Call your model **once**.
3. If the model wants tools → return `Plan(tool_calls=[ToolCall(...)])`.
4. If the model is done → return `Plan(final=True, output=...)`.

The engine commits the plan, dispatches the tools (idempotently, durably),
commits the observations, and calls your model function again with the updated
`state.messages`. You never write the loop, the retries, or the crash handling.

### Worked example with Claude

This wraps a Claude tool-calling turn as an Avatar model function. The model
choice is yours — Avatar is LLM-agnostic — but Avatar's own tooling defaults to
the latest Claude models, so we use `claude-opus-4-8` with adaptive thinking
here.

```python
# myagent_llm.py
import json
import anthropic
from avatar import Plan, ToolCall, agent, tool, current_idempotency_key

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the env

# 1. Your real tools — same as any Avatar tool. Forward the idempotency key on
#    anything with a side effect.
@tool(timeout=10, retries=2)
def issue_refund(order_id: str, cents: int) -> dict:
    key = current_idempotency_key()
    # stripe.Refund.create(..., idempotency_key=key)
    return {"refunded": True, "order_id": order_id, "cents": cents}

# The tool schema you hand the model. Keep names in sync with your @tool names.
TOOLS = [{
    "name": "issue_refund",
    "description": "Issue a refund for an order. Call this once the amount is known.",
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "cents": {"type": "integer", "description": "Refund amount in cents"},
        },
        "required": ["order_id", "cents"],
    },
}]

def _to_anthropic(state) -> list[dict]:
    """Translate Avatar's ledger-rebuilt transcript into Claude messages."""
    out: list[dict] = [{"role": "user", "content": json.dumps(state.input)}]
    for m in state.messages[1:]:
        if m["role"] == "assistant":
            blocks = [{"type": "text", "text": m.get("content") or "..."}]
            for tc in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": tc["id"],
                               "name": tc["name"], "input": tc["arguments"]})
            out.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            out.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            }]})
    return out

# 2. The model function: ONE Claude turn → ONE Avatar Plan.
@agent("support-resolver")
def resolve(state) -> Plan:
    resp = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=1024,
        thinking={"type": "adaptive"},
        tools=TOOLS,
        messages=_to_anthropic(state),
    )
    cost = (resp.usage.input_tokens + resp.usage.output_tokens) // 1000  # rough cents

    tool_calls = [
        ToolCall(id=b.id, name=b.name, arguments=b.input)
        for b in resp.content if b.type == "tool_use"
    ]
    if tool_calls:
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return Plan(content=text, tool_calls=tool_calls, cost_cents=cost)

    # No tool calls → the model is done.
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return Plan(final=True, output={"answer": text}, cost_cents=cost)
```

Run it exactly like Path A (`AVATAR_APP=myagent_llm avatar serve` / `worker`),
and enqueue with the SDK. Now your existing Claude agent is crash-safe,
replayable, and idempotent — and you didn't write a single line of durability
code.

Notes for binding real agents:

- **Tool-call ids come from the model** (`b.id`). The engine commits them to the
  ledger, so they're crash-stable once a plan commits — that's what keeps
  idempotency correct across a mid-dispatch crash.
- **One model call per `@agent` invocation.** Don't run your own tool loop inside
  the model function — return the `Plan` and let the engine dispatch. That's what
  makes each step a durable, replayable ledger entry.
- **Keep the model function a pure function of `state`** as much as you can.
  Determinism is what makes [replay/fork](AVATAR.md) reproduce a run without
  re-calling the model or re-running tools.

---

## The idempotency contract (the one rule that matters)

Avatar guarantees **at-most-once dispatch from its side, always**. Exactly-once
*end-to-end* requires your tool to honor the key:

```python
@tool(idempotent=True)
def issue_refund(order_id: str, cents: int) -> dict:
    key = current_idempotency_key()      # stable across a crash re-dispatch
    return charge_provider.refund(order_id, cents, idempotency_key=key)
```

Forward `current_idempotency_key()` to any downstream that supports an
idempotency key (Stripe's `Idempotency-Key`, your own `UNIQUE` constraint, etc.).
On a crash between dispatch and observation, Avatar re-dispatches with the
**same** key and the downstream dedupes. Without that, a re-dispatch can produce
a second real effect — Avatar can't prevent what it can't see.

---

## Watch it survive a crash yourself

The fastest way to believe the guarantee is to cause the crash. Avatar's demo
agent supports a crash hook via `AVATAR_CRASH_AFTER_DISPATCH=<tool_name>`: a
worker started with it set will exit hard right after that tool dispatches but
before the observation commits. Start one worker with the hook (it dies), then a
second without it (it resumes) — the refund still happens exactly once. That is
precisely what `avatar demo` automates; read [cli.py](../avatar/cli.py) to see
the two-worker orchestration.

---

## Budgets, policy, and approvals

Three governance hooks, all optional, all documented in depth in
[AVATAR.md](AVATAR.md):

- **Budget** — pass `budget_cap_cents` on `runs.create`. The run hard-stops
  *before its next step* once `cost_cents` accumulates past the cap. (It does not
  cancel an in-flight call — treat it as a circuit breaker, not a pre-charge.)
- **Policy** — register a policy hook that returns `allow` / `deny` /
  `require_approval` before every tool dispatch.
- **Approvals** — a `require_approval` decision parks the run in `approval_wait`;
  resolve it with `runs.approve(id)` / `runs.reject(id)` (or the dashboard
  buttons), and the run resumes from the ledger.

---

## Production

When you're ready to deploy:

- Switch `AVATAR_DATABASE_URL` to Postgres and run `avatar migrate`.
- **Do not set `AVATAR_DEV_MODE`** in production — set a strong `AVATAR_API_KEY`
  (`openssl rand -hex 32`). The API and worker refuse to boot otherwise.
- Run behind TLS; gate the dashboard and `/metrics`.

The full production guide — Caddy/TLS compose, backups/PITR, scaling,
observability, tool-isolation caveats — is in
[docs/deployment.md](deployment.md) and [SECURITY.md](../SECURITY.md).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Worker exits: *"Refusing to start: AVATAR_API_KEY is unset or a known default"* | Production safety guard. Set `AVATAR_DEV_MODE=1` for local dev, or a strong `AVATAR_API_KEY`. |
| `runs.create` returns 401 | The client `api_key` doesn't match `AVATAR_API_KEY` on the server. |
| Run stays `queued`, nothing happens | No worker running, or the worker can't import `AVATAR_APP` (check it's on `PYTHONPATH` for the worker process). |
| `Unknown agent <ref>` | The `@agent("<ref>")` in your module doesn't match the `agent_ref` you enqueued, or `AVATAR_APP` isn't pointed at your module. |
| Tool dispatched twice → two real effects | Your tool isn't honoring `current_idempotency_key()`. Forward it downstream. |
| `runs.create` returns 429 | Backpressure: `AVATAR_MAX_QUEUE_DEPTH` reached or the per-process rate limit tripped. |

---

## Where to go next

- **[The Complete Guide](AVATAR.md)** — architecture, execution semantics, the
  concurrency model as theorems, the failure taxonomy, the Guarantees Spec, and
  the roadmap.
- **[Deployment](deployment.md)** — production hardening.
- **[Security](../SECURITY.md)** — the auth model and its limits.
- The [README](../README.md) — the SDK and Control API reference tables.
