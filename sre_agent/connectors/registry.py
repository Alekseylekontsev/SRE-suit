"""Connector registry.

Aggregates connectors, merges their operations into one namespace (raising on a
duplicate op name so two connectors can't silently shadow each other), and
exposes lookups for the executor plus a fanned-out health probe.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import ConfigError
from ..core.models import HealthStatus, OperationSpec
from .base import Connector
from .hetzner import HetznerConnector
from .proxmox import ProxmoxConnector


class ConnectorRegistry:
    """Holds the set of active connectors and their merged operation table."""

    def __init__(self) -> None:
        self._connectors: List[Connector] = []

    def register(self, connector: Connector) -> None:
        """Add a connector. Validates that its op names don't collide with an
        already-registered connector's (fail fast on duplicate)."""
        existing = self.operations()
        for op_name in connector.operations():
            if op_name in existing:
                raise ConfigError(
                    f"duplicate operation name {op_name!r}: already provided by "
                    f"another connector"
                )
        self._connectors.append(connector)

    def connectors(self) -> List[Connector]:
        return list(self._connectors)

    def operations(self) -> Dict[str, OperationSpec]:
        """Merged op-name -> OperationSpec across all connectors.

        Raises :class:`ConfigError` on a duplicate op name. (``register`` guards
        the incremental case; this guards any direct list manipulation too.)
        """
        merged: Dict[str, OperationSpec] = {}
        for connector in self._connectors:
            for op_name, spec in connector.operations().items():
                if op_name in merged:
                    raise ConfigError(f"duplicate operation name {op_name!r}")
                merged[op_name] = spec
        return merged

    def spec_for(self, op_name: str) -> Optional[OperationSpec]:
        return self.operations().get(op_name)

    def health(self) -> Dict[str, HealthStatus]:
        return {c.name: c.health() for c in self._connectors}


def build_from_config(config: Dict) -> ConnectorRegistry:
    """Construct a registry of Proxmox + Hetzner connectors from a config dict.

    Expects the existing config shape:
      - ``config["proxmox"]["nodes"]`` -> list of node dicts with
        ``name``/``host``/``token``/``verify_ssl``/``ca_cert``.
      - ``config["hetzner"]["token"]`` (+ optional ``endpoint``).
    A connector is only registered if it has usable config (at least one
    proxmox node, or a hetzner token).
    """
    registry = ConnectorRegistry()

    proxmox_cfg = (config or {}).get("proxmox", {}) or {}
    nodes_list = proxmox_cfg.get("nodes", []) or []
    nodes: Dict[str, Dict] = {}
    for node in nodes_list:
        host = node.get("host")
        if not host or not node.get("token"):
            continue
        name = node.get("name", host)
        nodes[name] = {
            "host": host,
            "token": node.get("token"),
            "verify_ssl": node.get("verify_ssl", True),
            "ca_cert": node.get("ca_cert"),
        }
    if nodes:
        registry.register(ProxmoxConnector(nodes))

    hetzner_cfg = (config or {}).get("hetzner", {}) or {}
    token = hetzner_cfg.get("token")
    if token:
        registry.register(
            HetznerConnector(
                token,
                endpoint=hetzner_cfg.get("endpoint", "https://api.hetzner.cloud/v1"),
            )
        )

    return registry


__all__ = ["ConnectorRegistry", "build_from_config"]
