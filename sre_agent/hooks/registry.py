"""Hook registry and execution engine.

The registry holds hooks keyed by lifecycle point and runs them in registration
order. Semantics for :meth:`HookRegistry.run_point`:

* Hooks run in the order they were registered for the given point.
* A ``DENY`` short-circuits: no further hooks run and that DENY is returned.
* A ``MUTATE`` merges its ``mutated_data`` into ``ctx.data`` and continues, so
  later hooks observe the mutated state.
* If no hook denies, the aggregate outcome is ALLOW.

``run_point`` never raises on DENY — the *caller* decides what a denial means.
Use :meth:`HookRegistry.enforce` when a denial should hard-stop the action; it
raises :class:`HookDeniedError`.
"""
from __future__ import annotations

from typing import Dict, List

from sre_agent.core.errors import HookDeniedError

from .base import Hook, HookContext, HookDecision, HookOutcome, HookPoint


class HookRegistry:
    """Registers hooks and executes them at a lifecycle point."""

    def __init__(self) -> None:
        self._hooks: Dict[HookPoint, List[Hook]] = {point: [] for point in HookPoint}

    def register(self, hook: Hook) -> None:
        """Register ``hook`` for every point declared in ``hook.points``."""
        if not hook.points:
            raise ValueError(f"hook {hook.name!r} declares no points")
        for point in hook.points:
            self._hooks[point].append(hook)

    def hooks_for(self, point: HookPoint) -> List[Hook]:
        """Return the hooks registered for ``point`` (in registration order)."""
        return list(self._hooks[point])

    def run_point(self, point: HookPoint, ctx: HookContext) -> HookOutcome:
        """Run all hooks for ``point`` in order; return the aggregate outcome.

        Returns the first DENY encountered (short-circuiting), otherwise ALLOW.
        MUTATE outcomes are applied to ``ctx.data`` in place as they occur.
        """
        for hook in self._hooks[point]:
            outcome = hook.run(ctx)
            if outcome.decision == HookDecision.DENY:
                return outcome
            if outcome.decision == HookDecision.MUTATE and outcome.mutated_data is not None:
                ctx.data.update(outcome.mutated_data)
        return HookOutcome.allow()

    def enforce(self, point: HookPoint, ctx: HookContext) -> HookOutcome:
        """Like :meth:`run_point`, but raise :class:`HookDeniedError` on DENY."""
        outcome = self.run_point(point, ctx)
        if outcome.decision == HookDecision.DENY:
            hook_name = ""
            # Best-effort: surface which hook denied, if discoverable by reason.
            for hook in self._hooks[point]:
                if getattr(hook, "name", "") and hook.name in (outcome.reason or ""):
                    hook_name = hook.name
                    break
            raise HookDeniedError(outcome.reason, hook=hook_name)
        return outcome


__all__ = ["HookRegistry"]
