"""Rate-limiting hook.

A fixed-window limiter keyed by ``action.name``. Allows up to ``max_calls``
calls per ``per_seconds`` window; the call that would exceed the limit is
DENIED. Time is read through an injectable ``time_fn`` seam so tests can drive
the clock deterministically.

The window is a simple sliding count: timestamps older than ``per_seconds`` are
evicted on each call, then the remaining count is checked. This avoids the
"reset boundary" burst problem of naive fixed windows.
"""
from __future__ import annotations

import time as _time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Optional

from ..base import Hook, HookContext, HookOutcome, HookPoint


class RateLimit(Hook):
    """PRE_ACTION hook enforcing per-action-name call rate limits."""

    name = "rate_limit"
    points = {HookPoint.PRE_ACTION}

    def __init__(
        self,
        max_calls: int,
        per_seconds: float,
        time_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        if per_seconds <= 0:
            raise ValueError("per_seconds must be > 0")
        self.max_calls = max_calls
        self.per_seconds = float(per_seconds)
        self._time_fn: Callable[[], float] = time_fn or _time.monotonic
        self._windows: Dict[str, Deque[float]] = defaultdict(deque)

    def _key(self, ctx: HookContext) -> str:
        return ctx.action.name if ctx.action is not None else "<no-action>"

    def run(self, ctx: HookContext) -> HookOutcome:
        now = self._time_fn()
        key = self._key(ctx)
        window = self._windows[key]

        # Evict timestamps outside the current window.
        cutoff = now - self.per_seconds
        while window and window[0] <= cutoff:
            window.popleft()

        if len(window) >= self.max_calls:
            return HookOutcome.deny(
                f"{self.name}: rate limit exceeded for {key!r} "
                f"({self.max_calls}/{self.per_seconds}s)"
            )

        window.append(now)
        return HookOutcome.allow()


__all__ = ["RateLimit"]
