# Governance

Avatar Engine is **company-led open source**, stewarded by the Avatar Runtime
Authors. It is the open-source foundation of the commercial **Avatar Cloud**
offering (the Temporal model: an open engine + a hosted control plane).

## Principles

- **Single-purpose.** Avatar Engine is a durable execution engine for AI agents.
  Changes must strengthen that wedge (durability, correctness, ergonomics,
  observability) — not broaden it into a platform. Multi-tenant/SaaS features
  belong in Avatar Cloud, not here.
- **Open to contribution.** Anyone may open issues and pull requests. Good-faith
  contributions are reviewed on technical merit.
- **Correctness first.** The crash-safety and idempotency guarantees are the
  product. Changes that touch the engine core require tests (including the
  crash-resume slice) and a clear statement of how the guarantees are preserved.

## Roles

- **Maintainers** — review and merge PRs, triage issues, cut releases, and own
  the roadmap and the public API contract. Listed in `.github/CODEOWNERS`.
- **Contributors** — anyone who submits an accepted change.

Maintainership is granted by existing maintainers to contributors with a
sustained track record of high-quality, on-mission contributions.

## Decision-making

- Routine changes: lazy consensus — a maintainer approval + green CI merges.
- Significant changes (public API, schema, the guarantees, new dependencies):
  open an issue first; require agreement from at least two maintainers.
- The maintainers are the final decision-makers; disputes are resolved by them.

## Releases

Semantic versioning. The v0.1 public API/SDK surface is the contract; breaking
changes require a minor (pre-1.0) bump and a CHANGELOG entry. See `CHANGELOG.md`.

## Security

Report vulnerabilities privately per `SECURITY.md`. Do not open public issues for
security reports.
