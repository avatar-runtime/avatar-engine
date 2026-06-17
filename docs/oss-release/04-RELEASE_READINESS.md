# Phase 9 — Release Readiness Review

**Date:** 2026-06-17
**Repository:** `github.com/avatar-runtime/avatar-engine` (to be pushed).

## Legal
- [x] **No proprietary code.** Only the engine + supporting OSS files; `ARCHIVED/`
      excluded entirely (zero references anywhere in the tree).
- [x] **Correct license.** Apache-2.0 (`LICENSE` + `NOTICE`), declared in
      `pyproject.toml` (PEP 639 `license = "Apache-2.0"`), SPDX headers on all
      Python files.
- [x] **Dependency licenses reviewed** — all permissive; `psycopg` (LGPL, optional)
      flagged. See `03-DEPENDENCY_LICENSES.md`.
- [ ] **Trademark review of "Avatar"** — *action for the company*: confirm the name
      is clear for public OSS use (a common word; consider a wordmark/scoping).

## Security
- [x] **No secrets / credentials / private keys / cloud IDs** — `detect-secrets`
      + manual sweeps clean; only dev placeholders remain. See `02-SECURITY_SCAN.md`.
- [x] **No internal endpoints** — internal container name removed.
- [x] **Brand-new git history** — the ≈96-commit SaaS history is not present/recoverable.
- [ ] **Enable GitHub secret scanning + push protection** on the public repo (action).
- [ ] **Add a `gitleaks` CI step** (maintained scanner; not preinstalled here) (action).

## Technical
- [x] **Tests pass** — 22 tests green on SQLite locally; CI also runs them on Postgres.
- [x] **Lint clean** — `ruff check .` passes.
- [x] **Demo works** — `python -m avatar.cli demo` → refund issued exactly once.
- [x] **Package builds & installs** — `python -m build` produces a wheel+sdist;
      `pip install` exposes the `avatar` CLI and `import avatar` works.
- [ ] **Docker build** — Dockerfile validated by review (LICENSE/NOTICE copied,
      OCI labels added); the daemon was unavailable in the extraction environment,
      so the `docker` CI job is the gating check on first push.

## Product
- [x] **README complete** — positioned as "Temporal for AI agents" with the
      flagship crash-resume demo front and center; badges, clone, license.
- [x] **Quickstart** — `pip install -e .` + `avatar demo`; `docker compose up`.
- [x] **Docs** — `docs/AVATAR.md` (complete guide), `docs/deployment.md`,
      `SECURITY.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `CODE_OF_CONDUCT.md`.

## Remaining actions before/at public launch (owner: company)
1. Trademark sanity-check on "Avatar".
2. Replace `@avatar-runtime/maintainers` in `.github/CODEOWNERS` with real handles.
3. Turn on secret scanning + push protection; add `gitleaks` to CI.
4. Configure **PyPI Trusted Publishing** for `avatar-runtime` (the `release.yml`
      workflow publishes on tag `v*`).
5. First push → confirm all CI jobs (lint, SQLite, Postgres, demo, docker) green.
6. Tag `v0.1.0` to publish to PyPI + GHCR.
