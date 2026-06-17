# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""Avatar control API (FastAPI) — single-key auth + SSE."""

from __future__ import annotations

from avatar.api.app import create_app

__all__ = ["create_app"]
