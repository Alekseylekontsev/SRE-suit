"""Drift detection — compare a declared desired state against the live graph.

The desired state is small and declarative (loaded from config). Detection is
pure: it never touches a connector. Remediation is *proposed* by emitting
:class:`ChangeSet` objects with ``origin="drift"`` that re-enter the normal
change-control gate — drift never executes anything itself.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from sre_agent.changecontrol.base import ChangeItem, ChangeSet

from .graph import ResourceGraph


@dataclass
class DesiredResource:
    """A declared expectation about one resource."""
    kind: str
    id: str
    name: str = ""
    connector: str = ""
    expected_status: Optional[str] = None
    expected_attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.connector}:{self.kind}:{self.id}"


@dataclass
class DesiredState:
    """A bundle of declared expectations."""
    resources: List[DesiredResource] = field(default_factory=list)

    @staticmethod
    def from_config(config: Dict[str, Any]) -> "DesiredState":
        """Read ``config["dataplane"]["desired"]`` (a list of dicts).

        Tolerant: returns an empty state if the section is absent or malformed.
        """
        resources: List[DesiredResource] = []
        if not isinstance(config, dict):
            return DesiredState(resources=resources)
        items = (config.get("dataplane") or {}).get("desired")
        if not isinstance(items, list):
            return DesiredState(resources=resources)
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            rid = item.get("id")
            if kind is None or rid is None:
                continue
            attrs = item.get("expected_attributes")
            resources.append(DesiredResource(
                kind=str(kind),
                id=str(rid),
                name=str(item.get("name", "")),
                connector=str(item.get("connector", "")),
                expected_status=item.get("expected_status"),
                expected_attributes=dict(attrs) if isinstance(attrs, dict) else {},
            ))
        return DesiredState(resources=resources)


class DriftKind(str, Enum):
    MISSING = "missing"            # desired but absent from the graph
    UNEXPECTED = "unexpected"      # in graph but not desired (closed-world only)
    STATUS_DRIFT = "status_drift"  # present but status != expected
    ATTRIBUTE_DRIFT = "attribute_drift"  # present but an attribute != expected


@dataclass
class DriftFinding:
    kind: DriftKind
    resource_key: str
    detail: str
    severity: str = "medium"


def detect_drift(desired: DesiredState, graph: ResourceGraph,
                 closed_world: bool = False) -> List[DriftFinding]:
    """Compare ``desired`` against ``graph`` and return findings.

    With ``closed_world=True`` the desired set is authoritative: any live
    resource without a matching desired entry is reported as UNEXPECTED.
    """
    findings: List[DriftFinding] = []
    desired_keys = set()

    for want in desired.resources:
        key = want.key
        desired_keys.add(key)
        actual = graph.get(key)
        if actual is None:
            findings.append(DriftFinding(
                kind=DriftKind.MISSING,
                resource_key=key,
                detail=f"desired {want.kind} {want.id} not present in graph",
                severity="high",
            ))
            continue
        if want.expected_status is not None and actual.status != want.expected_status:
            findings.append(DriftFinding(
                kind=DriftKind.STATUS_DRIFT,
                resource_key=key,
                detail=(f"status is {actual.status!r}, "
                        f"expected {want.expected_status!r}"),
                severity="medium",
            ))
        for attr, expected_value in want.expected_attributes.items():
            if actual.attributes.get(attr) != expected_value:
                findings.append(DriftFinding(
                    kind=DriftKind.ATTRIBUTE_DRIFT,
                    resource_key=key,
                    detail=(f"attribute {attr!r} is "
                            f"{actual.attributes.get(attr)!r}, "
                            f"expected {expected_value!r}"),
                    severity="low",
                ))

    if closed_world:
        for resource in graph.all():
            if resource.key not in desired_keys:
                findings.append(DriftFinding(
                    kind=DriftKind.UNEXPECTED,
                    resource_key=resource.key,
                    detail=f"{resource.kind} {resource.id} present but not desired",
                    severity="medium",
                ))

    return findings


_OP_BY_KIND = {
    DriftKind.MISSING: "create_resource",
    DriftKind.STATUS_DRIFT: "reconcile_status",
    DriftKind.ATTRIBUTE_DRIFT: "reconcile_attribute",
    DriftKind.UNEXPECTED: "review_unexpected_resource",
}


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def drift_to_changesets(findings: List[DriftFinding]) -> List[ChangeSet]:
    """Turn findings into proposed :class:`ChangeSet` objects.

    These are proposals only — nothing is executed. Each ChangeSet gets a
    deterministic id derived from its index and resource key (no randomness or
    timestamps) so the same drift produces stable, dedupable ids.
    """
    changesets: List[ChangeSet] = []
    for i, finding in enumerate(findings):
        operation = _OP_BY_KIND.get(finding.kind, "reconcile_resource")
        cs_id = f"drift-{i}-{_short_hash(finding.resource_key)}"
        item = ChangeItem(
            operation=operation,
            target=finding.resource_key,
            params={
                "drift_kind": finding.kind.value,
                "detail": finding.detail,
                "severity": finding.severity,
            },
            rationale=f"drift remediation: {finding.detail}",
        )
        changesets.append(ChangeSet(
            id=cs_id,
            title=f"Remediate {finding.kind.value} on {finding.resource_key}",
            items=[item],
            description=finding.detail,
            origin="drift",
            risk=finding.severity,
        ))
    return changesets


__all__ = [
    "DesiredResource",
    "DesiredState",
    "DriftKind",
    "DriftFinding",
    "detect_drift",
    "drift_to_changesets",
]
