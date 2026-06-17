# Phase 1 — OSS Extraction Audit

**Date:** 2026-06-17
**Source:** the combined private repository (engine + archived SaaS).
**Target:** `github.com/avatar-runtime/avatar-engine` (Apache-2.0, clean history).

The source repository already separated the two products cleanly: the engine in
`avatar/` + supporting files, and the proprietary SaaS in `ARCHIVED/`. This
audit classifies every top-level path.

## OSS inventory — included in Avatar Engine

| Path | What it is |
|---|---|
| `avatar/engine/` | Durable core: models, schema, runtime loop, worker (lease/heartbeat/resume), idempotency, tools, policy, budget, replay, registry. |
| `avatar/api/` | FastAPI control API (single-key auth), SSE, rate limiting, `/metrics`, `/v1/stats`. |
| `avatar/sdk/` | `@agent`/`@tool` decorators + REST/SSE client. |
| `avatar/cli.py` | `avatar serve | worker | migrate | demo`. |
| `avatar/config.py` | Env-only settings + startup-safety key check. |
| `avatar/demo.py` | The crash-resume killer demo (agent + idempotent refund tool). |
| `dashboard/` | Single-page dashboard + landing page. |
| `tests/` | Engine, crash-resume, idempotency, replay, policy/budget, API, hardening. |
| `docs/` | The complete guide, deployment, this release audit. |
| `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`, `Caddyfile` | Local + production (TLS) stacks. |
| `pyproject.toml`, `requirements.txt`, `pytest.ini` | Packaging, deps, test config. |
| `README.md`, `SECURITY.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `GOVERNANCE.md` | Docs + governance. |
| `.env.example`, `.env.prod.example`, `.dockerignore`, `.gitignore` | Config templates. |
| `.github/` | Issue templates, PR template, CODEOWNERS, CI + release workflows. |

## Proprietary inventory — excluded (stays in Avatar Cloud)

Everything under `ARCHIVED/` was **excluded entirely** and is **not present in
this repository or its git history**:

- Multi-tenancy: organizations, users, RBAC, JWT/SSO/SCIM.
- Billing/payments (incl. Paystack), usage metering, plans.
- Marketplace, workforce, templates, AI Humans, voice/video, widgets.
- BYOK vault / secret broker / key management.
- Connectors / MCP / mesh / gateway / multi-agent orchestration.
- RAG / knowledge bases / memory systems.
- Enterprise: DLP, WORM audit, SIEM, KYA identity.
- The legacy desktop avatar pipeline and inference servers.

## Gray-area inventory — reviewed, decision recorded

| Item | Decision | Rationale |
|---|---|---|
| `avatar/demo.py` | **Include** | It *is* the flagship demo; pure engine usage, no proprietary code. |
| Docs that mentioned `ARCHIVED/` | **Include, scrubbed** | Reworded to reference "Avatar Cloud" / "the commercial repo"; zero `ARCHIVED` references remain. |
| `docker-compose.yml` internal note (`vyra_db`) | **Include, scrubbed** | Internal container name removed; comment genericized. |
| `.env*` / compose dev credentials (`dev-key`, `avatar:avatar`) | **Include** | Documented dev placeholders; the app refuses to boot with them outside dev mode. |
| `docs/AVATAR.md` Cloud/roadmap sections | **Include** | They describe the *commercial roadmap* (no proprietary code), useful context for adopters. |

## Method (Phase 2)

A **fresh repository with brand-new history** was created by copying only the
approved OSS paths into a clean directory and running `git init` + a single
initial commit. The source repository's history (≈96 commits spanning the SaaS)
is **not** included and is **not recoverable** from this repository.
