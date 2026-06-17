-- Avatar canonical schema (reference documentation).
--
-- The live schema is created from the SQLAlchemy models via `avatar migrate`
-- (create_all), so it always matches the engine exactly. This file documents
-- that shape for review; tests/test_schema_drift.py pins it to the models
-- (table + column names) so it cannot silently drift. Types here mirror the
-- models' PORTABLE types (varchar/json/text) — the same on Postgres and SQLite
-- — not native uuid/jsonb/enum, so status filters and id inserts behave
-- identically on both. Versioned migrations (post-v1) are Alembic's job.

CREATE TABLE runs (
  id                varchar(32) PRIMARY KEY,        -- uuid4 hex, app-generated
  agent_ref         varchar(200) NOT NULL,          -- which agent definition/version
  status            varchar(20) NOT NULL DEFAULT 'queued',
  input             json NOT NULL,
  output            json,
  cursor_seq        int  NOT NULL DEFAULT 0,         -- last committed step seq
  lease_owner       varchar(80),
  lease_expires_at  timestamptz,                     -- heartbeated, NULL when unleased
  attempt           int  NOT NULL DEFAULT 0,         -- incremented on each re-lease
  budget_cap_cents  int,                             -- NULL = unlimited
  budget_used_cents int  NOT NULL DEFAULT 0,
  error_class       varchar(20),                     -- model|tool|policy|budget|infra|cancelled
  cancel_requested  boolean NOT NULL DEFAULT false,
  idempotency_key   varchar(200) UNIQUE,             -- caller-supplied, dedups enqueue
  forked_from       varchar(32),                     -- replay provenance
  fork_seq          int,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Dispatch hot path: the queue scan.
CREATE INDEX runs_dispatch_idx ON runs (status, created_at);

CREATE TABLE run_steps (
  id              varchar(32) PRIMARY KEY,           -- uuid4 hex, app-generated
  run_id          varchar(32) NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  seq             int  NOT NULL,
  type            varchar(20) NOT NULL,
  payload         json NOT NULL,
  tool_call_id    varchar(80),                       -- stable id from the model's tool call
  idempotency_key varchar(120),                      -- derived, see idempotency.py
  cost_cents      int  NOT NULL DEFAULT 0,
  worker_id       varchar(80),                       -- who committed it, resume markers
  attempt         int  NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, seq),                              -- strict gap-free ordering
  UNIQUE (run_id, idempotency_key)                   -- the exactly-once-record guarantee
);

CREATE INDEX ix_runstep_run ON run_steps (run_id, seq);

-- Human-in-the-loop decisions for require_approval tool calls. The ledger stays
-- the source of truth for execution; this side table records the out-of-band
-- human decision the engine reads when it re-leases a parked run.
CREATE TABLE approvals (
  id            varchar(32) PRIMARY KEY,             -- uuid4 hex, app-generated
  run_id        varchar(32) NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  tool_call_id  varchar(80) NOT NULL,
  tool_name     varchar(200) NOT NULL,
  arguments     json NOT NULL,
  status        varchar(20) NOT NULL DEFAULT 'pending',   -- pending|approved|rejected
  decided_by    varchar(80),
  created_at    timestamptz NOT NULL DEFAULT now(),
  decided_at    timestamptz,
  UNIQUE (run_id, tool_call_id)
);

CREATE INDEX ix_approval_run ON approvals (run_id);

-- RULES (enforced by the engine, documented here):
--   * run_steps rows are APPEND-ONLY. Never UPDATE a payload. Never DELETE.
--   * cursor_seq advances in the SAME transaction as the step insert.
--   * a tool's observation is recorded under UNIQUE(run_id, idempotency_key),
--     making it impossible to record a side effect twice.
