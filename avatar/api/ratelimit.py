# Copyright 2026 Avatar Runtime Authors
# SPDX-License-Identifier: Apache-2.0

"""A tiny in-process token-bucket rate limiter for the control API.

Single static key ⇒ a single global bucket is sufficient (per-key == global).
This guards the write path (enqueue) against a client flooding the queue. It is
intentionally process-local: with multiple API replicas, set the limit per
replica or front the API with a gateway. For per-tenant limits, see the
Avatar Cloud roadmap.
"""

from __future__ import annotations

import time


class TokenBucket:
    def __init__(self, rate_per_second: float, burst: int) -> None:
        self.rate = max(0.0, rate_per_second)
        self.capacity = max(1, burst)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        """Consume ``cost`` tokens if available. Returns False when throttled.
        A non-positive rate disables limiting (always allow)."""
        if self.rate <= 0:
            return True
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False
