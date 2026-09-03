"""In-process sliding-window rate limiting.

A dict in memory, not Redis. The pilot is one process; adding Redis would be an
abstraction with a single implementation, which the brief explicitly rules out.
If this ever runs multi-process, replace this module -- it is deliberately the
only thing that would need replacing.

The critical rule: exceeding a limit raises `service_busy()`, which is a **503**.
A 429 is a 4xx, and the client permanently drops the ping on any non-401 4xx
(brief 5.3). Rate limiting a flood-time location upload into oblivion would
destroy exactly the data the system exists to collect.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)


def allow(key: str, limit: int, window_seconds: int = 3600) -> bool:
    """Record a hit and report whether it is within the limit."""
    now = time.monotonic()
    cutoff = now - window_seconds
    with _lock:
        bucket = _hits[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def reset() -> None:
    """Test hook."""
    with _lock:
        _hits.clear()
