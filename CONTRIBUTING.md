# Contributing to Avatar

Thank you for your interest in contributing! This document provides guidelines for contributing to the Avatar project.

## Getting Started

1. **Fork** the repository on GitHub
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/Mwaivictor/Avatar.git
   cd avatar
   ```
3. **Create a branch** for your work:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Install dependencies** (editable, with dev + Postgres extras):
   ```bash
   pip install -e ".[dev,postgres]"
   ```

## Development Workflow

### Making Changes

1. Make your changes in your feature branch
2. Test your changes locally (SQLite is the default; run against Postgres for
   the crash/race slice's true `FOR UPDATE SKIP LOCKED` path):
   ```bash
   pytest -q
   AVATAR_DATABASE_URL=postgresql+asyncpg://avatar:avatar@localhost:5432/avatar pytest -q
   ```
3. Ensure the crash-resume guarantee still holds end-to-end:
   ```bash
   python -m avatar.cli demo
   ```

### Commit Messages

Use clear, descriptive commit messages:

```
feat: add new voice profile selection UI
fix: resolve camera capture timeout on Windows
docs: update API endpoint documentation
refactor: simplify face tracker initialization
```

Prefixes:
- `feat:` — New feature
- `fix:` — Bug fix
- `docs:` — Documentation only
- `refactor:` — Code refactoring (no behavior change)
- `test:` — Adding or updating tests
- `chore:` — Build, CI, or tooling changes

### Pull Requests

1. Push your branch to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```
2. Open a Pull Request against the `main` branch
3. Describe what your PR does and why
4. Link any related issues

## What to Contribute

Avatar is **single-purpose infrastructure** — a durable execution engine for AI
agents. Contributions should strengthen the wedge (durability, correctness,
ergonomics), not broaden it back toward a platform. Features outside the
in-scope list belong in **Avatar Cloud** (the commercial, hosted layer
built on top of this engine), not in the open-source engine.

### Good First Issues

- More fault-injection points and crash-window tests
- Documentation and example agents/tools
- Dashboard UX (timeline, filters, accessibility)
- A TypeScript SDK mirroring the Python client surface

### Feature Ideas (in-scope)

- Alembic baseline migration alongside `engine/schema.sql`
- Per-tool egress allowlist in `subprocess` isolation
- Backoff/jitter strategies for tool retries
- Prometheus metrics and structured logs for runs/workers

### Bug Reports

When reporting bugs, include:
- Python version (`python --version`)
- Operating system and version
- Docker version (`docker --version`)
- Steps to reproduce
- Error messages / stack traces
- Expected vs actual behavior

## Code Style

- Follow PEP 8 for Python code
- Use type hints for function signatures
- Keep functions focused and small
- Add docstrings to public classes and methods
- Use `logging` instead of `print()` for output

## Project Structure

- `app/capture/` — Input capture (webcam, microphone)
- `app/tracking/` — Face detection and expression analysis
- `app/services/` — AI inference service clients
- `app/rendering/` — Frame compositing and A/V sync
- `app/output/` — Virtual device output
- `app/api/` — REST API and web interface
- `inference_servers/` — Docker-based AI model servers
- `static/` — Web dashboard files
- `tests/` — Test suite

## AI Model Contributions

If contributing new AI models:
1. Create a new directory under `inference_servers/`
2. Include a `Dockerfile`, `server.py`, and `requirements.txt`
3. Follow the existing health-check endpoint pattern (`GET /health`)
4. Document model requirements and expected input/output formats

## Questions?

Open an issue on GitHub with the `question` label, or start a discussion in the Discussions tab.

---

Thank you for helping make Avatar better!
