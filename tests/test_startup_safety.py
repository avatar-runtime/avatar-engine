# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""The production safety guard: refuse to boot with an insecure API key."""

from __future__ import annotations

import pytest

from avatar.config import InsecureConfigError, Settings, check_startup_safety

PG = "postgresql+asyncpg://u:p@db.example.com:5432/avatar"


def _settings(**kw) -> Settings:
    base = dict(database_url=PG, api_key="a-strong-unique-key", dev_mode=False)
    base.update(kw)
    return Settings(**base)


def test_default_key_rejected_in_production():
    with pytest.raises(InsecureConfigError):
        check_startup_safety(_settings(api_key="dev-key"))


def test_empty_key_rejected_in_production():
    with pytest.raises(InsecureConfigError):
        check_startup_safety(_settings(api_key=""))


def test_strong_key_allowed_in_production():
    check_startup_safety(_settings(api_key="b7c1f0e9d2a3..."))  # no raise


def test_default_key_allowed_in_dev_mode():
    check_startup_safety(_settings(api_key="dev-key", dev_mode=True))  # no raise
