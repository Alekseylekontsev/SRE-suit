"""Dataplane — resource knowledge layer and drift detection.

``graph`` normalizes connector discovery output into a queryable
:class:`ResourceGraph`; ``drift`` compares a declared desired state against
that graph and proposes (never executes) remediation change sets.
"""
from __future__ import annotations

from .drift import (
    DesiredResource,
    DesiredState,
    DriftFinding,
    DriftKind,
    detect_drift,
    drift_to_changesets,
)
from .graph import (
    Resource,
    ResourceGraph,
    build_from_coordinator,
    build_from_inventory,
)

__all__ = [
    "Resource",
    "ResourceGraph",
    "build_from_inventory",
    "build_from_coordinator",
    "DesiredResource",
    "DesiredState",
    "DriftKind",
    "DriftFinding",
    "detect_drift",
    "drift_to_changesets",
]
