# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Control API: single-key auth, enqueue, list/get/steps, replay, 404s."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from avatar.api.app import create_app
from avatar.config import load_settings

AUTH = {"Authorization": "Bearer test-key"}


@pytest_asyncio.fixture
async def client():
    app = create_app(load_settings())
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


@pytest.mark.asyncio
async def test_auth_required(client):
    assert (await client.get("/v1/runs")).status_code == 401
    assert (await client.get("/v1/runs", headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await client.get("/v1/runs", headers=AUTH)).status_code == 200


@pytest.mark.asyncio
async def test_enqueue_and_fetch(client):
    r = await client.post("/v1/runs", headers=AUTH,
                          json={"agent_ref": "refund-demo", "input": {"order_id": "x"},
                                "budget_cap_cents": 500})
    assert r.status_code == 202
    rid = r.json()["id"]
    assert r.json()["status"] == "queued"

    got = await client.get(f"/v1/runs/{rid}", headers=AUTH)
    assert got.status_code == 200
    assert got.json()["agent_ref"] == "refund-demo"

    lst = await client.get("/v1/runs", headers=AUTH)
    assert any(run["id"] == rid for run in lst.json()["runs"])

    steps = await client.get(f"/v1/runs/{rid}/steps", headers=AUTH)
    assert steps.status_code == 200 and steps.json() == []


@pytest.mark.asyncio
async def test_idempotent_enqueue(client):
    body = {"agent_ref": "refund-demo", "input": {}, "idempotency_key": "dedup-1"}
    a = await client.post("/v1/runs", headers=AUTH, json=body)
    b = await client.post("/v1/runs", headers=AUTH, json=body)
    assert a.json()["id"] == b.json()["id"]


@pytest.mark.asyncio
async def test_not_found(client):
    assert (await client.get("/v1/runs/nope", headers=AUTH)).status_code == 404


@pytest.mark.asyncio
async def test_health(client):
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200
