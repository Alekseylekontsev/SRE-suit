"""Deterministic operation classification → autonomy tiers.

Pure-Python, no LLM. Maps an operation name to an :class:`AutonomyTier` using
config lists, optionally raised (never lowered) by a connector's intrinsic
tier. Unknown operations are APPROVE (fail-closed). When an operation appears
in conflicting tiers, the most restrictive wins.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .models import AutonomyTier, OperationType

# config "operations" list name → tier
_LIST_TIERS = {
    "read_only": AutonomyTier.AUTO,
    "write_safe": AutonomyTier.NOTIFY,
    "approval_required": AutonomyTier.APPROVE,
    "pr_only": AutonomyTier.PR_ONLY,
}


class Classifier:
    """Classifies operations into autonomy tiers from config."""

    def __init__(self, config: Dict):
        self._ops_cfg: Dict[str, List[str]] = config.get("operations", {}) or {}
        self._emergency = set(
            config.get("security", {}).get("emergency_auto_actions", []) or []
        )

    def classify(self, operation: str, intrinsic: Optional[AutonomyTier] = None) -> AutonomyTier:
        """Return the autonomy tier for ``operation``.

        Considers every config list the op appears in plus an optional
        ``intrinsic`` tier (e.g. a connector's OperationSpec.tier); the most
        restrictive wins. Unknown + no intrinsic → APPROVE (fail-closed).
        """
        tiers: List[AutonomyTier] = []
        for list_name, tier in _LIST_TIERS.items():
            if operation in (self._ops_cfg.get(list_name, []) or []):
                tiers.append(tier)
        if intrinsic is not None:
            tiers.append(intrinsic)
        if not tiers:
            return AutonomyTier.APPROVE  # unknown → fail-closed
        return AutonomyTier.most_restrictive(tiers)

    def classify_legacy(self, operation: str) -> OperationType:
        """Back-compat: return the legacy OperationType for ``operation``."""
        return OperationType.from_tier(self.classify(operation))

    def requires_approval(self, operation: str, intrinsic: Optional[AutonomyTier] = None) -> bool:
        return self.classify(operation, intrinsic) in (AutonomyTier.APPROVE, AutonomyTier.PR_ONLY)

    def is_emergency_allowed(self, operation: str) -> bool:
        return operation in self._emergency


def validate(config: Dict, known_ops: Optional[Iterable[str]] = None) -> List[str]:
    """Return human-readable classification problems (empty list == clean).

    Flags: an operation listed in conflicting tiers; an emergency auto-action
    that is not a known operation / has no handler.
    """
    problems: List[str] = []
    ops_cfg = config.get("operations", {}) or {}

    seen: Dict[str, str] = {}
    for list_name in _LIST_TIERS:
        for op in ops_cfg.get(list_name, []) or []:
            if op in seen and seen[op] != list_name:
                problems.append(
                    f"operation {op!r} is listed in both {seen[op]!r} and {list_name!r}; "
                    "the more restrictive tier will win"
                )
            seen[op] = list_name

    emergency = config.get("security", {}).get("emergency_auto_actions", []) or []
    known = set(known_ops) if known_ops is not None else set(seen)
    for op in emergency:
        if op not in known:
            problems.append(
                f"emergency_auto_action {op!r} is not a known operation / has no handler"
            )
    return problems


__all__ = ["Classifier", "validate"]
