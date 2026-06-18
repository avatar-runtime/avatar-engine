# Troubleshooting

Common first-run issues and how to fix them. Most reduce to one of three things:
**all processes must share one database**, **the worker must import your app**,
and **a worker must actually be running**.

---

## The dashboard says "no runs yet (0)" / my runs don't appear

You enqueued a run (or ran a script/demo) but the dashboard's Runs table is
empty, or your run never shows up.

**The dashboard shows runs from the database the API server is using.** If your
run was created against a *different* database than `avatar serve` is reading,
the API — and therefore the dashboard — correctly shows zero runs. This is
almost always one of the following.

### Cause 1 — the API, the worker, and your enqueuer are on different databases

`AVATAR_DATABASE_URL` defaults to `sqlite+aiosqlite:///./avatar.db`, a **relative
path**. SQLite resolves it against each process's **current working directory**,
so an API started in `~/project` and a script run from `~/project/scripts` use
**two different files** (`~/project/avatar.db` vs `~/project/scripts/avatar.db`).
They can't see each other's runs.

Tests and throwaway demos often point at a temp DB on purpose (e.g. a file under
`/tmp`), so runs they create are invisible to a separately-started
`avatar serve` — that is expected, not a bug.

**Fix:** give every process the *same* `AVATAR_DATABASE_URL`, and for SQLite use
an **absolute path** so the working directory can't matter:

```bash
export AVATAR_DATABASE_URL="sqlite+aiosqlite:////absolute/path/to/avatar.db"
#                                            ^^^^ four slashes = absolute path
```

Or move to Postgres (recommended for anything beyond a single process — see
[deployment.md](deployment.md)), where every process connects to the same server
regardless of working directory:

```bash
export AVATAR_DATABASE_URL="postgresql+asyncpg://avatar:avatar@localhost:5432/avatar"
```

Verify all three see the same data:

```bash
curl -s -H "Authorization: Bearer $AVATAR_API_KEY" http://localhost:8080/v1/runs | jq '.runs | length'
```

### Cause 2 — the worker wasn't told which app to load (`AVATAR_APP`)

If you enqueue `agent_ref="my-agent"` but the worker was started without
`AVATAR_APP` pointing at the module that runs your `@agent` / `@tool`
decorators, the worker can't find the agent. The run is leased and then **fails
with `error_class=infra` and `unknown agent_ref: my-agent`** — so it appears in
the list as `failed`, not `succeeded`.

```bash
# the worker MUST import your app so the registry is populated
AVATAR_APP=yourpkg.agents avatar worker
```

Check a failed run's reason:

```bash
curl -s -H "Authorization: Bearer $AVATAR_API_KEY" \
  http://localhost:8080/v1/runs/$RUN_ID | jq '{status, error_class, output}'
```

### Cause 3 — no worker is running

`avatar serve` only hosts the control API + dashboard; **it does not execute
runs.** With no worker, runs sit in `queued` forever. Start at least one:

```bash
AVATAR_APP=yourpkg.agents avatar worker        # scale by running more
```

A run stuck in `queued` with no worker, or `running`/`leased` with a
stale/absent worker, is the tell-tale.

---

## Minimal end-to-end that *does* show up in the dashboard

Run each in the same directory with the **same env** (three terminals, or `&`):

```bash
export AVATAR_DATABASE_URL="sqlite+aiosqlite:////tmp/avatar.db"   # absolute!
export AVATAR_DEV_MODE=1                # local only: allows dev-key, injects it into the UI
export AVATAR_API_KEY=dev-key
export AVATAR_APP=yourpkg.agents        # the module with your @agent/@tool

avatar migrate                          # create the schema once
avatar serve --port 8080                # terminal 1: API + dashboard
avatar worker                           # terminal 2: executes runs (note AVATAR_APP)

# terminal 3: enqueue something
python -c "from avatar import Avatar; \
  print(Avatar(api_key='dev-key').runs.create(agent_ref='yourpkg-agent', input={}))"
```

Open <http://localhost:8080> → the run appears, click it for the step-ledger
timeline, and use **Fork here** / **Approve** / **Reject** as applicable.

---

## Other common issues

### "Refusing to start: AVATAR_API_KEY is unset or a known default"

The API/worker refuse to boot in **non-dev mode** with `dev-key` (or empty), to
avoid shipping a wide-open deployment. For local dev set `AVATAR_DEV_MODE=1`; for
anything real set a strong key and leave dev mode off:

```bash
export AVATAR_API_KEY="$(openssl rand -hex 32)"   # and run behind TLS
```

See [SECURITY.md](../SECURITY.md).

### The dashboard loads but every API call is 401

The dashboard injects the API key automatically **only in dev mode**
(`AVATAR_DEV_MODE=1`). Outside dev mode you must supply
`Authorization: Bearer $AVATAR_API_KEY` yourself (the dashboard is a thin static
client; put it behind your own auth/proxy in production).

### A run is `dead`

A run reaches `dead` after `AVATAR_MAX_ATTEMPTS` re-leases — a *poison run* the
engine stopped retrying (e.g. a tool that crashes the worker every attempt).
Inspect its steps (`GET /v1/runs/{id}/steps`), fix the tool, and enqueue a fresh
run. Alert on `dead` and on `failed` with `error_class=infra` in production.

### A tool "succeeded" but its `observation` never committed / the run errored on commit

Tool return values are stored verbatim in the `run_steps.payload` JSON column, so
they must be **JSON-native**. Returning a `datetime`, `Decimal`, set, or other
non-serializable object can abort the observation commit. Return plain
dicts/lists/strings/numbers (convert datetimes to ISO strings, `Decimal` to
int/float) from your tools.

### Two workers seem to run the same run / duplicate effects

Single ownership is enforced by the lease (`FOR UPDATE SKIP LOCKED` on Postgres,
compare-and-swap on SQLite). Two genuine concerns:

- **SQLite under real concurrency.** SQLite is for dev/tests; run **Postgres** for
  the true `SKIP LOCKED` path with multiple workers.
- **Exactly-once is conditional.** Avatar guarantees *at-most-once dispatch*; a
  re-dispatch in the crash window is exactly-once **only if your tool honors the
  idempotency key**. Forward `current_idempotency_key()` to your downstream (e.g.
  Stripe's `Idempotency-Key`). See the idempotency section in the
  [README](../README.md) and [AVATAR.md](AVATAR.md).

### A run is stuck in `approval_wait`

A `require_approval` policy decision parks the run until a human resolves it.
Resolve via the dashboard buttons or the API:

```bash
curl -s -X POST -H "Authorization: Bearer $AVATAR_API_KEY" \
  http://localhost:8080/v1/runs/$RUN_ID/approve     # or /reject
```

---

## Quick diagnosis checklist

```bash
# 1. Is the API healthy and which DB is it on?
curl -s http://localhost:8080/healthz; curl -s http://localhost:8080/readyz
echo "$AVATAR_DATABASE_URL"

# 2. Does the API see any runs at all?
curl -s -H "Authorization: Bearer $AVATAR_API_KEY" http://localhost:8080/v1/runs | jq '.runs | length'

# 3. Is a worker running, and was it given AVATAR_APP?
#    (check the worker's startup log line: "avatar worker <host:pid> started (db=...)")

# 4. For a specific run, what happened?
curl -s -H "Authorization: Bearer $AVATAR_API_KEY" \
  http://localhost:8080/v1/runs/$RUN_ID | jq '{status, error_class, output}'
curl -s -H "Authorization: Bearer $AVATAR_API_KEY" \
  http://localhost:8080/v1/runs/$RUN_ID/steps | jq '.[] | {seq, type, tool_call_id}'
```

If the DB in step 1 differs between your API, worker, and enqueuer, that's the
problem — see [Cause 1](#cause-1--the-api-the-worker-and-your-enqueuer-are-on-different-databases).
