# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""``avatar`` CLI: ``worker``, ``serve``, ``demo``."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys

from avatar.config import load_settings


def _cmd_worker(args: argparse.Namespace) -> int:
    if args.max_runs:
        os.environ["AVATAR_WORKER_MAX_RUNS"] = str(args.max_runs)
    if args.max_idle:
        os.environ["AVATAR_WORKER_MAX_IDLE_SECONDS"] = str(args.max_idle)
    from avatar.engine.worker import run_forever

    asyncio.run(run_forever())
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "avatar.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    """Apply the canonical schema (schema.sql on Postgres) idempotently."""
    from avatar.engine.db import create_engine, migrate

    settings = load_settings()

    async def _run() -> bool:
        engine = create_engine(settings.database_url, settings)
        try:
            return await migrate(engine)
        finally:
            await engine.dispose()

    created = asyncio.run(_run())
    print("schema created" if created else "schema already present — no-op")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    return run_demo(quick=args.quick)


def run_demo(quick: bool = False) -> int:
    """Orchestrate the crash-resume demo with real worker processes.

    1. enqueue a refund run; 2. start a worker primed to crash mid-refund;
    3. after it dies, start a fresh worker that resumes from the ledger;
    4. assert the refund happened exactly once and print the timeline.
    """
    import asyncio as _asyncio

    from sqlalchemy import select

    from avatar import demo as demo_mod
    from avatar.engine.db import create_engine, create_session_factory, init_db
    from avatar.engine.models import AgentRun, AgentRunStep
    from avatar.engine.registry import load_app

    # Fast lease so the resume is quick to watch.
    os.environ.setdefault("AVATAR_LEASE_SECONDS", "2")
    os.environ["AVATAR_APP"] = "avatar.demo"
    settings = load_settings()
    load_app()

    print("== Avatar killer demo: crash-safe refund ==\n")
    print(f"db: {settings.database_url}")

    async def _setup() -> str:
        engine = create_engine(settings.database_url)
        await init_db(engine)
        factory = create_session_factory(engine)
        async with factory() as db:
            run = AgentRun(agent_ref="refund-demo", input={"order_id": "order-42"},
                           status="queued", budget_cap_cents=1000)
            db.add(run)
            await db.commit()
            rid = run.id
        await engine.dispose()
        return rid

    demo_mod.reset_store()
    run_id = _asyncio.run(_setup())
    print(f"enqueued run {run_id}\n")

    base_env = {**os.environ, "AVATAR_APP": "avatar.demo",
                "AVATAR_LEASE_SECONDS": os.environ["AVATAR_LEASE_SECONDS"],
                "AVATAR_WORKER_MAX_RUNS": "1", "AVATAR_WORKER_MAX_IDLE_SECONDS": "20"}

    # --- worker #1: crashes after dispatching the refund ---
    print("starting worker #1 (will crash mid-refund)…")
    crash_env = {**base_env, "AVATAR_CRASH_AFTER_DISPATCH": "issue_refund"}
    w1 = subprocess.run([sys.executable, "-m", "avatar.cli", "worker", "--max-runs", "1"],
                        env=crash_env)
    print(f"worker #1 exited with code {w1.returncode} "
          f"({'crashed as planned' if w1.returncode != 0 else 'did not crash!'})\n")

    # --- worker #2: fresh process resumes from the ledger ---
    print("starting worker #2 (resumes from the ledger)…")
    w2 = subprocess.run([sys.executable, "-m", "avatar.cli", "worker", "--max-runs", "1"],
                        env=base_env)
    print(f"worker #2 exited with code {w2.returncode}\n")

    # --- assertions + timeline ---
    async def _report() -> tuple:
        engine = create_engine(settings.database_url)
        factory = create_session_factory(engine)
        async with factory() as db:
            run = (await db.execute(select(AgentRun).where(AgentRun.id == run_id))).scalar_one()
            steps = (await db.execute(
                select(AgentRunStep).where(AgentRunStep.run_id == run_id)
                .order_by(AgentRunStep.seq))).scalars().all()
        await engine.dispose()
        return run, steps

    run, steps = _asyncio.run(_report())
    se = demo_mod.side_effect_count("issue_refund")
    dc = demo_mod.dispatch_count("issue_refund")

    print("---- timeline ----")
    prev_attempt = None
    for s in steps:
        if prev_attempt is not None and s.attempt != prev_attempt:
            print(f"   ▸ resumed by {s.worker_id} (attempt {s.attempt})")
        prev_attempt = s.attempt
        print(f"  #{s.seq:<2} [{s.type}] {s.tool_call_id or ''} (attempt {s.attempt})")
    print("------------------\n")

    print(f"run status        : {run.status}")
    print(f"dispatch attempts : {dc}  (the tool was physically called this many times)")
    print(f"tool effects      : {se}  (actual refunds that happened)")
    print(f"budget used (cents): {run.budget_used_cents}\n")

    ok = run.status == "succeeded" and se == 1
    if ok:
        print('✅ "Crashed mid-refund. Restarted. The refund wasn\'t issued twice."')
        print("   Tool dispatch attempts may repeat, but tool effects cannot")
        print("   duplicate when idempotency is enforced.")
    else:
        print("❌ demo assertions FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="avatar", description="Avatar durable execution engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("worker", help="run a durable worker")
    w.add_argument("--max-runs", type=int, default=0, help="exit after N runs (0=forever)")
    w.add_argument("--max-idle", type=float, default=0, help="exit after N idle seconds")
    w.set_defaults(func=_cmd_worker)

    s = sub.add_parser("serve", help="run the control API + dashboard")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(func=_cmd_serve)

    m = sub.add_parser("migrate", help="apply the canonical schema (idempotent)")
    m.set_defaults(func=_cmd_migrate)

    d = sub.add_parser("demo", help="run the crash-resume killer demo")
    d.add_argument("--quick", action="store_true")
    d.set_defaults(func=_cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
