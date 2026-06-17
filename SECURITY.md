# Security

Avatar is **single-tenant infrastructure you self-host**. Its trust boundary is
your own network and your Postgres database. Read this before exposing it.

## Reporting a vulnerability

Email the maintainers (see the repository owner) with details and a repro. Please
do not open a public issue for a security report.

## The authentication model (know its limits)

- The control API is protected by **one static bearer key**, `AVATAR_API_KEY`,
  compared in constant time. There are **no users, roles, or tenants** — anyone
  with the key has full control of every run.
- This is intentional for the wedge: the boundary is *your* deployment. If you
  need per-user isolation, you need the (not-yet-shipped) multi-tenant layer; do
  **not** hand the key to untrusted parties as a substitute.

## Production must-dos

1. **Set a strong `AVATAR_API_KEY`.** The app and worker **refuse to boot** in
   non-dev mode if the key is unset or a known default (`dev-key`, etc.). Generate
   one with `openssl rand -hex 32`. Only `AVATAR_DEV_MODE=1` (local only) relaxes
   this.
2. **Never expose the API port directly.** Put it behind a TLS reverse proxy.
   `docker-compose.prod.yml` ships Caddy (automatic HTTPS) and does **not** publish
   the API or Postgres ports — only Caddy's 80/443.
3. **Gate the dashboard and `/metrics`.** `/app` and `/metrics` sit behind HTTP
   basic-auth in the Caddy config. In production the dashboard ships with **no key
   embedded** in its HTML (it prompts the operator and stores the key in the
   browser's localStorage), so serving `/app` does not leak the API key — but
   basic-auth in front of it is still required.
4. **Lock down Postgres.** It holds the ledger — the entire system of record. Use a
   strong `POSTGRES_PASSWORD`, keep it off the public internet, and back it up
   (see [docs/deployment.md](docs/deployment.md)).
5. **Restrict `/metrics`.** It is unauthenticated at the app layer (so a scraper
   can reach it) and is protected only by your network policy / the reverse proxy.
   Do not expose it publicly.

## Running agent/tool code — important

- Tools execute **in the worker process by default** (`AVATAR_TOOL_ISOLATION=inproc`).
  A tool that crashes the interpreter (segfault, `os._exit`) **takes the worker
  down**. This is acceptable for **trusted, first-party** tools only.
- For anything less trusted, set `AVATAR_TOOL_ISOLATION=subprocess`: each tool
  runs in a child process with a wall-clock timeout and an output-size cap.
- **There is no network/filesystem sandbox.** SSRF, egress filtering, and
  metadata-IP blocking were deliberately cut from the wedge. **Do not run untrusted
  third-party agent code on Avatar yet.** A tool can reach anything the worker's
  network can reach.

## Idempotency & the exactly-once boundary

Avatar guarantees **at-most-once dispatch** of a tool from its side, and
re-dispatches a crash-window call with the *same* `Idempotency-Key`. End-to-end
exactly-once holds **iff your tool/downstream honors that key** (forward
`avatar.current_idempotency_key()`). Tools with non-idempotent side effects that
ignore the key can still double-apply on a crash — that is a property of the
downstream, not of Avatar.

## Budget caps stop runs, not in-flight calls

`budget_cap_cents` halts a run **before its next step** once the cap is reached.
The model/tool call already in flight when the cap is hit is **not** cancelled —
its provider cost is already incurred. Treat the cap as a circuit breaker, not a
hard pre-charge.
