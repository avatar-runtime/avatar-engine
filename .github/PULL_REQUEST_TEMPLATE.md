## What & why

<!-- What does this change do, and why? Link any related issue. -->

## How the guarantees are preserved

<!-- If you touched the engine core (runtime/worker/idempotency/replay), explain
how crash-safety, single-owner execution, and at-most-once dispatch still hold. -->

## Checklist

- [ ] Tests added/updated (and `pytest` passes locally)
- [ ] If engine-core: the crash-resume test still passes (`python -m avatar.cli demo`)
- [ ] `ruff check .` is clean
- [ ] Docs updated if behavior/API changed
- [ ] No secrets, credentials, or internal endpoints added
- [ ] Change is in-scope for the engine (not an Avatar Cloud / SaaS feature)
