"""Connector interface.

A :class:`Connector` exposes a set of named operations, each described by an
:class:`OperationSpec` that binds an op name to a handler
``Callable[[Action], Any]`` plus its intrinsic :class:`AutonomyTier` and
idempotency flag. Handlers read params from ``action.payload``, return
JSON-able results, and raise :class:`ConnectorError` on bad input (missing
node, non-int id, unimplemented op) rather than leaking bare exceptions.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Set

from ..core.models import HealthStatus, OperationSpec


class Connector(ABC):
    """Base class every connector implements.

    Subclasses set :attr:`name` and implement :meth:`operations`,
    :meth:`capabilities`, and :meth:`health`.
    """

    #: Stable, unique connector name (used by the registry).
    name: str = "connector"

    @abstractmethod
    def operations(self) -> Dict[str, OperationSpec]:
        """Return the operations this connector exposes, keyed by op name."""
        raise NotImplementedError

    @abstractmethod
    def capabilities(self) -> Set[str]:
        """Return a set of capability tags (e.g. ``{"compute", "snapshot"}``)."""
        raise NotImplementedError

    @abstractmethod
    def health(self) -> HealthStatus:
        """Probe the connector/target and return its current health."""
        raise NotImplementedError


__all__ = ["Connector"]
