"""Destructive-action guard hook.

Denies destructive operations (delete VM/container/server/backup/snapshot, etc)
unless the request is *explicitly* confirmed. "Explicitly confirmed" means one
of:

* the action payload carries ``"confirm": true``, or
* the action has already cleared human approval (``action.status == "APPROVED"``).

The rule is deliberately conservative and fail-closed: an unconfirmed delete is
denied. The set of destructive op names is configurable via the constructor.
"""
from __future__ import annotations

from typing import Iterable, Optional, Set

from sre_agent.core.models import AutonomyTier

from ..base import Hook, HookContext, HookOutcome, HookPoint

DEFAULT_DESTRUCTIVE_OPS: Set[str] = {
    "delete_vm",
    "delete_container",
    "hetzner_delete_server",
    "delete_backup",
    "delete_snapshot",
}


class DestructiveGuard(Hook):
    """PRE_ACTION hook that blocks unconfirmed destructive operations."""

    name = "destructive_guard"
    points = {HookPoint.PRE_ACTION}

    def __init__(self, destructive_ops: Optional[Iterable[str]] = None) -> None:
        self.destructive_ops: Set[str] = (
            set(destructive_ops) if destructive_ops is not None else set(DEFAULT_DESTRUCTIVE_OPS)
        )

    def run(self, ctx: HookContext) -> HookOutcome:
        action = ctx.action
        if action is None or action.name not in self.destructive_ops:
            return HookOutcome.allow()

        # Confirmed explicitly in the payload.
        if action.payload.get("confirm") is True:
            return HookOutcome.allow()

        # Already approved by a human (the approval layer flipped status).
        if action.status == "APPROVED":
            return HookOutcome.allow()

        approve_note = ""
        if ctx.tier is AutonomyTier.APPROVE:
            approve_note = " (tier APPROVE requires status APPROVED)"
        return HookOutcome.deny(
            f"{self.name}: destructive op {action.name!r} requires "
            f"explicit confirm=true or prior approval" + approve_note
        )


__all__ = ["DestructiveGuard", "DEFAULT_DESTRUCTIVE_OPS"]
