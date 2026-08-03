"""Core hook contracts for the SRE Agent policy layer.

A *hook* is a small, single-purpose policy that runs at a well-defined point in
the action lifecycle (before/after an action, before approval, on audit). Each
hook inspects a :class:`HookContext` and returns a :class:`HookOutcome` telling
the caller to ALLOW, DENY, or MUTATE the in-flight data.

These types are intentionally behavior-light so connectors, the executor, and
the approval layer can depend on them without import cycles.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Set

from sre_agent.core.models import Action, AutonomyTier


class HookPoint(Enum):
    """Lifecycle points at which hooks may run."""
    PRE_ACTION = "pre_action"
    POST_ACTION = "post_action"
    PRE_APPROVAL = "pre_approval"
    ON_AUDIT = "on_audit"


class HookDecision(str, Enum):
    """The verdict a hook returns. A str-enum so it serializes cleanly."""
    ALLOW = "ALLOW"
    DENY = "DENY"
    MUTATE = "MUTATE"


@dataclass
class HookContext:
    """Everything a hook needs to make a decision.

    ``data`` is free-form and carries point-specific payloads (the action
    result for POST_ACTION, an audit record for ON_AUDIT, etc). Hooks read from
    and (via MUTATE) write to it.
    """
    point: HookPoint
    action: Optional[Action] = None
    tier: Optional[AutonomyTier] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HookOutcome:
    """The result of running a hook."""
    decision: HookDecision
    reason: str = ""
    mutated_data: Optional[Dict[str, Any]] = None

    @classmethod
    def allow(cls, reason: str = "") -> "HookOutcome":
        return cls(decision=HookDecision.ALLOW, reason=reason)

    @classmethod
    def deny(cls, reason: str) -> "HookOutcome":
        return cls(decision=HookDecision.DENY, reason=reason)

    @classmethod
    def mutate(cls, data: Dict[str, Any], reason: str = "") -> "HookOutcome":
        return cls(decision=HookDecision.MUTATE, reason=reason, mutated_data=data)


class Hook(ABC):
    """Base class for all hooks.

    Subclasses set ``points`` (the lifecycle points they care about) and a
    human-readable ``name``, then implement :meth:`run`.
    """
    points: Set[HookPoint] = set()
    name: str = "hook"

    @abstractmethod
    def run(self, ctx: HookContext) -> HookOutcome:
        """Inspect ``ctx`` and return an ALLOW/DENY/MUTATE outcome."""
        raise NotImplementedError


__all__ = [
    "HookPoint",
    "HookDecision",
    "HookContext",
    "HookOutcome",
    "Hook",
]
