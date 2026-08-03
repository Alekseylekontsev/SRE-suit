"""Change-control contract + a local file backend.

A :class:`ChangeSet` is a connector-agnostic description of one or more
intended changes. A :class:`ChangeControlBackend` turns it into a reviewable
:class:`ChangeRequest`. The contract is deliberately small so executor and
backends depend only on it.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChangeItem:
    """A single intended change (an operation that would mutate state)."""
    operation: str
    target: str
    params: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class ChangeSet:
    """A reviewable bundle of intended changes."""
    id: str
    title: str
    items: List[ChangeItem] = field(default_factory=list)
    description: str = ""
    origin: str = "action"  # "action" | "drift"
    risk: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


@dataclass
class ChangeRequest:
    """The result of proposing a ChangeSet."""
    id: str
    backend: str
    status: str = "proposed"  # proposed | open | merged | failed
    url: Optional[str] = None
    detail: str = ""


class ChangeControlBackend(ABC):
    """Opens a reviewable change request from a ChangeSet."""
    name: str = "base"

    @abstractmethod
    def propose(self, change: ChangeSet) -> ChangeRequest: ...


class FileChangeControlBackend(ChangeControlBackend):
    """Writes each ChangeSet as a JSON artifact into a directory.

    A simple, network-free GitOps-lite backend: the directory can be a checked-
    out repo whose commits/PRs are handled by an external CI/Git workflow. Also
    the default for tests and air-gapped environments.
    """
    name = "file"

    def __init__(self, directory: str):
        self._dir = directory

    def propose(self, change: ChangeSet) -> ChangeRequest:
        try:
            os.makedirs(self._dir, exist_ok=True)
            path = os.path.join(self._dir, f"{change.id}.json")
            # 0644 is fine — changesets are not secrets (and are scrubbed upstream)
            with open(path, "w", encoding="utf-8") as f:
                f.write(change.to_json())
        except OSError as e:
            return ChangeRequest(id=change.id, backend=self.name, status="failed", detail=str(e))
        return ChangeRequest(id=change.id, backend=self.name, status="proposed",
                             url=f"file://{os.path.abspath(path)}")


__all__ = [
    "ChangeItem",
    "ChangeSet",
    "ChangeRequest",
    "ChangeControlBackend",
    "FileChangeControlBackend",
]
