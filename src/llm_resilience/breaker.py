"""A per-provider circuit breaker.

closed     -> calls go through; consecutive provider failures are counted.
open       -> calls are skipped until `recovery_timeout` has passed.
half_open  -> one trial call is let through; success closes, failure re-opens.

Without this, every request during an outage pays the full timeout on the dead
provider before falling back.
"""

from __future__ import annotations

from .clock import SystemClock

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, recovery_timeout: float = 30.0, clock=None) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.clock = clock or SystemClock()
        self._state = CLOSED
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        if self._state == OPEN and self.clock.now() - self._opened_at >= self.recovery_timeout:
            self._state = HALF_OPEN
        return self._state

    def allow(self) -> bool:
        return self.state != OPEN

    def record_success(self) -> None:
        self._state = CLOSED
        self._failures = 0

    def record_failure(self) -> None:
        if self.state == HALF_OPEN:
            self._trip()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._trip()

    def _trip(self) -> None:
        self._state = OPEN
        self._opened_at = self.clock.now()
        self._failures = 0
