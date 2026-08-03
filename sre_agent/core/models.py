"""Shared data models for the SRE Agent control plane.

These types are the stable contract every other package imports. Keep them
free of behavior beyond simple helpers so connectors, secrets, hooks, and
approvals can depend on them without cycles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class AutonomyTier(Enum):
    """How much autonomy an operation is granted (replaces the old flat
    read/write/approval/emergency split).

    Ordering matters: a higher value is MORE restrictive. When config places an
    operation in conflicting tiers, the more restrictive tier wins.
    """
    AUTO = 0       # read-only / explicitly safe: run immediately
    NOTIFY = 1     # low-risk write: run, but emit a (non-blocking) notification
    APPROVE = 2    # risky/destructive: require human approval (fail-closed)
    PR_ONLY = 3    # high blast radius: must go through GitOps change control

    @classmethod
    def most_restrictive(cls, tiers: "List[AutonomyTier]") -> "AutonomyTier":
        """Return the most restrictive tier in ``tiers`` (default APPROVE)."""
        if not tiers:
            return cls.APPROVE
        return max(tiers, key=lambda t: t.value)


# Backwards-compatible alias of the legacy classification enum. The old code
# (and ported tests) refer to OperationType; map the four legacy names onto the
# new tiers so shims can translate without behavioral change.
class OperationType(Enum):
    READ_ONLY = "read_only"
    WRITE_SAFE = "write_safe"
    APPROVAL_REQUIRED = "approval_required"
    EMERGENCY = "emergency"

    def to_tier(self) -> AutonomyTier:
        return {
            OperationType.READ_ONLY: AutonomyTier.AUTO,
            OperationType.WRITE_SAFE: AutonomyTier.NOTIFY,
            OperationType.APPROVAL_REQUIRED: AutonomyTier.APPROVE,
            OperationType.EMERGENCY: AutonomyTier.AUTO,
        }[self]

    @classmethod
    def from_tier(cls, tier: AutonomyTier) -> "OperationType":
        return {
            AutonomyTier.AUTO: cls.READ_ONLY,
            AutonomyTier.NOTIFY: cls.WRITE_SAFE,
            AutonomyTier.APPROVE: cls.APPROVAL_REQUIRED,
            AutonomyTier.PR_ONLY: cls.APPROVAL_REQUIRED,
        }[tier]


@dataclass
class Action:
    """A single requested operation against a connector."""
    id: str
    name: str
    target: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: str = "PENDING"  # PENDING, APPROVED, DECLINED, EXECUTING, DONE, FAILED, PENDING_APPROVAL, BLOCKED_EMERGENCY
    requested_by: Optional[str] = None  # who submitted it (for separation-of-duties)
    approver: Optional[str] = None  # identity recorded when ApprovalManager approves it


@dataclass
class Batch:
    """A group of actions presented for approval together."""
    batch_id: str
    actions: List[Action] = field(default_factory=list)
    window_minutes: int = 180
    created_at: Optional[str] = None
    approve_all: bool = False

    def add_action(self, action: Action) -> None:
        self.actions.append(action)


@dataclass
class OperationResult:
    """Result of executing a single action."""
    action_id: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class OperationSpec:
    """Describes one operation a connector exposes.

    ``handler`` takes an :class:`Action` and returns a JSON-serializable result.
    ``tier`` is the operation's *intrinsic* autonomy tier (config may raise it,
    never lower it). ``idempotent`` gates whether retries/PR-replays are safe.
    ``secret_keys`` lists secret names the executor must JIT-lease before the
    call.
    """
    name: str
    handler: Callable[[Action], Any]
    tier: AutonomyTier = AutonomyTier.APPROVE
    idempotent: bool = False
    secret_keys: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class HealthStatus:
    """Health of a connector/target, consumed by monitoring + the SLO tracker."""
    healthy: bool
    detail: str = ""
    latency_ms: Optional[float] = None


@dataclass
class Lease:
    """A short-TTL secret lease. ``value`` is injected at call time and should
    not be logged, cached globally, or persisted."""
    key: str
    value: str
    expires_at: float  # epoch seconds (monotonic-independent wall clock)

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at

    def __repr__(self) -> str:  # never leak the value in logs/tracebacks
        return f"Lease(key={self.key!r}, expires_at={self.expires_at!r}, value=<redacted>)"


__all__ = [
    "AutonomyTier",
    "OperationType",
    "Action",
    "Batch",
    "OperationResult",
    "OperationSpec",
    "HealthStatus",
    "Lease",
]
