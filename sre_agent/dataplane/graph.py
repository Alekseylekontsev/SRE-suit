"""Resource knowledge layer — normalize raw connector output into a graph.

A :class:`Resource` is a connector-agnostic view of one piece of
infrastructure (a Proxmox node/VM, a Hetzner server, etc). A
:class:`ResourceGraph` is an in-memory index of them keyed by a stable
identity. Builders translate the agent's ``discover()`` output (which is
shaped by the connectors) into Resources, tolerating missing keys and
malformed entries — discovery data is never trusted to be well-formed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class Resource:
    """A normalized, connector-agnostic infrastructure resource."""
    kind: str  # node | vm | container | server | volume | network
    id: str
    name: str
    connector: str  # e.g. "proxmox" | "hetzner"
    status: str = "unknown"
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identity across discoveries."""
        return f"{self.connector}:{self.kind}:{self.id}"


class ResourceGraph:
    """An index of :class:`Resource` objects keyed by ``Resource.key``."""

    def __init__(self) -> None:
        self._resources: Dict[str, Resource] = {}

    def add(self, resource: Resource) -> None:
        self._resources[resource.key] = resource

    def get(self, key: str) -> Optional[Resource]:
        return self._resources.get(key)

    def by_kind(self, kind: str) -> List[Resource]:
        return [r for r in self._resources.values() if r.kind == kind]

    def by_connector(self, name: str) -> List[Resource]:
        return [r for r in self._resources.values() if r.connector == name]

    def all(self) -> List[Resource]:
        return list(self._resources.values())

    def keys(self) -> Set[str]:
        return set(self._resources.keys())

    def __len__(self) -> int:
        return len(self._resources)


# --- normalization helpers -------------------------------------------------

def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _normalize_proxmox_nodes(entries: Any, graph: ResourceGraph) -> None:
    """Proxmox ``list_nodes`` returns ``[{"node": name, "nodes": [...]}, ...]``."""
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        nodes = entry.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            nid = node.get("node") or node.get("id")
            if nid is None:
                continue
            graph.add(Resource(
                kind="node",
                id=_as_str(nid),
                name=_as_str(node.get("node") or nid),
                connector="proxmox",
                status=_as_str(node.get("status") or "unknown") or "unknown",
                attributes=dict(node),
            ))


def _normalize_proxmox_vms(entries: Any, graph: ResourceGraph) -> None:
    """Proxmox ``list_vms`` returns ``[{"node": name, "vms": [...]}, ...]``.

    Each vm carries a ``type`` of "qemu" (→ vm) or "lxc" (→ container).
    """
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        vms = entry.get("vms")
        if not isinstance(vms, list):
            continue
        node_name = entry.get("node")
        for vm in vms:
            if not isinstance(vm, dict):
                continue
            vmid = vm.get("vmid") or vm.get("id")
            if vmid is None:
                continue
            kind = "container" if vm.get("type") == "lxc" else "vm"
            attrs = dict(vm)
            if node_name is not None:
                attrs.setdefault("node", node_name)
            graph.add(Resource(
                kind=kind,
                id=_as_str(vmid),
                name=_as_str(vm.get("name") or vmid),
                connector="proxmox",
                status=_as_str(vm.get("status") or "unknown") or "unknown",
                attributes=attrs,
            ))


def _normalize_hetzner_servers(payload: Any, graph: ResourceGraph) -> None:
    """Hetzner ``hetzner_list_servers`` returns ``{"servers": [...]}``."""
    servers: Any
    if isinstance(payload, dict):
        servers = payload.get("servers")
    elif isinstance(payload, list):
        servers = payload
    else:
        return
    if not isinstance(servers, list):
        return
    for server in servers:
        if not isinstance(server, dict):
            continue
        sid = server.get("id")
        if sid is None:
            continue
        graph.add(Resource(
            kind="server",
            id=_as_str(sid),
            name=_as_str(server.get("name") or sid),
            connector="hetzner",
            status=_as_str(server.get("status") or "unknown") or "unknown",
            attributes=dict(server),
        ))


def build_from_inventory(inventory: Dict[str, Any]) -> ResourceGraph:
    """Build a :class:`ResourceGraph` from a ``discover()``-shaped inventory.

    Tolerant of missing keys, error payloads, and malformed entries — never
    raises on bad data, just skips what it can't parse.
    """
    graph = ResourceGraph()
    if not isinstance(inventory, dict):
        return graph
    _normalize_proxmox_nodes(inventory.get("list_nodes"), graph)
    _normalize_proxmox_vms(inventory.get("list_vms"), graph)
    _normalize_hetzner_servers(inventory.get("hetzner_list_servers"), graph)
    return graph


def build_from_coordinator(coordinator: Any) -> ResourceGraph:
    """Build a graph by calling ``coordinator.discover()``.

    Duck-typed to avoid importing :class:`Coordinator` at module load (which
    would create an import cycle through the control plane).
    """
    inventory = coordinator.discover()
    return build_from_inventory(inventory)


__all__ = [
    "Resource",
    "ResourceGraph",
    "build_from_inventory",
    "build_from_coordinator",
]
