"""Per-client sliding-window limit (in memory; one uvicorn worker). IPs are hashed, never stored raw."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute, self.clock = per_minute, clock
        self.hits: dict[str, deque] = defaultdict(deque)

    @staticmethod
    def key(ip: str) -> str:
        return hashlib.sha256(ip.encode()).hexdigest()[:16]

    def allow(self, ip: str) -> bool:
        now, q = self.clock(), self.hits[self.key(ip)]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True
