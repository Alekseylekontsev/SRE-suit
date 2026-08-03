"""Proxmox connector.

Wraps one :class:`BaseHTTPClient` per Proxmox node (mirroring the original
``SREAgent.__init__`` proxmox loop) and exposes the Proxmox operations that
previously lived as ``_handle_*`` methods in ``agent.py``. Node-resolution
boilerplate is consolidated into :meth:`_client_for`. Operations that were
"Not implemented" stubs in ``agent.py`` raise :class:`ConnectorError` so the
executor records ``success=False`` instead of returning a fake success dict.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Set, Tuple

from ..core.errors import ConnectorError
from ..core.models import Action, AutonomyTier, HealthStatus, OperationSpec
from .base import Connector
from .http import BaseHTTPClient

# Proxmox REST API roots at /api2/json; bake the prefix into the client base URL.
_API_PREFIX = "/api2/json"


class ProxmoxConnector(Connector):
    """Connector for one or more Proxmox VE nodes."""

    name = "proxmox"

    def __init__(self, nodes: Dict[str, Dict[str, Any]]):
        """Build one HTTP client per node.

        ``nodes`` maps a node name to a dict with ``host`` (https URL), ``token``,
        and optional ``verify_ssl`` / ``ca_cert`` — the same shape as the entries
        the original agent built clients from.
        """
        self._clients: Dict[str, BaseHTTPClient] = {}
        for node_name, cfg in (nodes or {}).items():
            host = cfg.get("host")
            token = cfg.get("token")
            if not host or not token:
                continue
            base = host.rstrip("/") + _API_PREFIX
            self._clients[node_name] = BaseHTTPClient(
                base,
                auth_header=("Authorization", f"PVEAPIToken={token}"),
                verify_ssl=cfg.get("verify_ssl", True),
                ca_cert=cfg.get("ca_cert"),
            )

    def read_clients(self) -> Dict[str, BaseHTTPClient]:
        """Per-node hardened HTTP clients (with ``_request``) for the monitoring
        read path — the single source of Proxmox connectivity."""
        return dict(self._clients)

    # ------------------------------------------------------------- helpers
    def _client_for(self, action: Action) -> Tuple[str, BaseHTTPClient]:
        """Resolve the node and its client from ``action.payload['node']``.

        Raises :class:`ConnectorError` if the node is missing or unknown — the
        node-resolution boilerplate that was duplicated across every handler.
        """
        node = action.payload.get("node")
        if not node:
            raise ConnectorError("node required")
        client = self._clients.get(node)
        if client is None:
            raise ConnectorError(f"unknown node: {node}")
        return node, client

    @staticmethod
    def _endpoint(vm_type: str) -> str:
        return "qemu" if vm_type == "qemu" else "lxc"

    @staticmethod
    def _require_vmid(action: Action) -> str:
        """Validate and return the vmid as a string of digits.

        Proxmox vmids are integers; reject non-numeric input (prior review).
        """
        vmid = action.payload.get("vmid")
        if vmid is None:
            raise ConnectorError("vmid required")
        try:
            int(vmid)
        except (TypeError, ValueError):
            raise ConnectorError(f"vmid must be an integer (got {vmid!r})")
        return str(vmid)

    @staticmethod
    def _unwrap(res: Dict[str, Any]) -> Any:
        """Return ``res['data']`` for a successful response, else raise.

        The hardened client returns ``{"error": ...}`` on failure; turn that into
        a ConnectorError so the operation reports success=False.
        """
        if isinstance(res, dict) and "error" in res:
            raise ConnectorError(str(res.get("error")))
        if isinstance(res, dict) and "data" in res:
            return res["data"]
        return res

    # ----------------------------------------------------------- read ops
    def _list_nodes(self, action: Action) -> List[Dict[str, Any]]:
        results = []
        for node_name, client in self._clients.items():
            res = client.request("GET", "/nodes")
            if isinstance(res, dict) and "error" in res:
                results.append({"node": node_name, "error": res["error"]})
            else:
                results.append({"node": node_name, "nodes": res.get("data", [])})
        return results

    def _list_vms(self, action: Action) -> List[Dict[str, Any]]:
        results = []
        for node_name, client in self._clients.items():
            qemu = client.request("GET", f"/nodes/{node_name}/qemu")
            lxc = client.request("GET", f"/nodes/{node_name}/lxc")
            if isinstance(qemu, dict) and "error" in qemu:
                results.append({"node": node_name, "error": qemu["error"]})
                continue
            vms = list(qemu.get("data", []))
            for vm in vms:
                vm["type"] = "qemu"
            cts = lxc.get("data", []) if isinstance(lxc, dict) else []
            for ct in cts:
                ct["type"] = "lxc"
            results.append({"node": node_name, "vms": vms + cts})
        return results

    def _list_containers(self, action: Action) -> List[Dict[str, Any]]:
        results = []
        for node_name, client in self._clients.items():
            res = client.request("GET", f"/nodes/{node_name}/lxc")
            if isinstance(res, dict) and "error" in res:
                results.append({"node": node_name, "error": res["error"]})
            else:
                results.append({"node": node_name, "containers": res.get("data", [])})
        return results

    def _get_vm_status(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        ep = self._endpoint(action.payload.get("type", "qemu"))
        return self._unwrap(client.request("GET", f"/nodes/{node}/{ep}/{vmid}/status"))

    def _get_node_metrics(self, action: Action) -> Any:
        node, client = self._client_for(action)
        return self._unwrap(client.request("GET", f"/nodes/{node}/status"))

    def _get_storage_usage(self, action: Action) -> List[Dict[str, Any]]:
        node, client = self._client_for(action)
        listing = client.request("GET", f"/nodes/{node}/storage")
        if isinstance(listing, dict) and "error" in listing:
            raise ConnectorError(str(listing["error"]))
        out = []
        for s in listing.get("data", []):
            storage = s.get("storage", s.get("id", ""))
            out.append(client.request("GET", f"/nodes/{node}/storage/{storage}/status"))
        return out

    def _list_snapshots(self, action: Action) -> Dict[str, Any]:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        ep = self._endpoint(action.payload.get("type", "qemu"))
        res = client.request("GET", f"/nodes/{node}/{ep}/{vmid}/snapshot")
        return {"snapshots": self._unwrap(res) or []}

    def _get_cluster_status(self, action: Action) -> List[Dict[str, Any]]:
        results = []
        for node_name, client in self._clients.items():
            res = client.request("GET", "/cluster/status")
            if isinstance(res, dict) and "error" in res:
                results.append({"node": node_name, "error": res["error"]})
            else:
                results.append({"node": node_name, "cluster_status": res})
        return results

    def _get_network_config(self, action: Action) -> Dict[str, Any]:
        node, client = self._client_for(action)
        res = client.request("GET", f"/nodes/{node}/network")
        return {"network": self._unwrap(res) or []}

    # --------------------------------------------------------- safe writes
    def _start_vm(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        ep = self._endpoint(action.payload.get("type", "qemu"))
        return self._unwrap(client.request("POST", f"/nodes/{node}/{ep}/{vmid}/status/start"))

    def _stop_vm(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        ep = self._endpoint(action.payload.get("type", "qemu"))
        return self._unwrap(client.request("POST", f"/nodes/{node}/{ep}/{vmid}/status/stop"))

    def _restart_vm(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        ep = self._endpoint(action.payload.get("type", "qemu"))
        return self._unwrap(client.request("POST", f"/nodes/{node}/{ep}/{vmid}/status/reboot"))

    def _create_snapshot(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        ep = self._endpoint(action.payload.get("type", "qemu"))
        snapname = action.payload.get("snapname")
        if not snapname:
            raise ConnectorError("snapname required")
        data: Dict[str, Any] = {"snapname": snapname}
        desc = action.payload.get("description")
        if desc:
            data["description"] = desc
        return self._unwrap(
            client.request("POST", f"/nodes/{node}/{ep}/{vmid}/snapshot", data=data)
        )

    # ------------------------------------------------- approval-required ops
    def _delete_vm(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        ep = self._endpoint(action.payload.get("type", "qemu"))
        return self._unwrap(client.request("DELETE", f"/nodes/{node}/{ep}/{vmid}"))

    def _delete_container(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        return self._unwrap(client.request("DELETE", f"/nodes/{node}/lxc/{vmid}"))

    def _delete_snapshot(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        ep = self._endpoint(action.payload.get("type", "qemu"))
        snapname = action.payload.get("snapname")
        if not snapname:
            raise ConnectorError("snapname required")
        return self._unwrap(
            client.request("DELETE", f"/nodes/{node}/{ep}/{vmid}/snapshot/{snapname}")
        )

    def _migrate_vm(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        ep = self._endpoint(action.payload.get("type", "qemu"))
        target = action.payload.get("target")
        if not target:
            raise ConnectorError("target node required for migration")
        data = {"target": target}
        if "online" in action.payload:
            data["online"] = action.payload["online"]
        return self._unwrap(
            client.request("POST", f"/nodes/{node}/{ep}/{vmid}/migrate", data=data)
        )

    def _create_vm(self, action: Action) -> Any:
        node, client = self._client_for(action)
        vmid = self._require_vmid(action)
        p = action.payload
        data = {
            "vmid": vmid,
            "name": p.get("name", ""),
            "ostype": p.get("ostype", "l26"),
            "cores": int(p.get("cores", 2)),
            "memory": int(p.get("memory", 2048)),
            "scsi0": f"{p.get('storage', 'local')}:{p.get('disk_size', '32G')}",
            "net0": p.get("net0", "virtio,bridge=vmbr0"),
            "boot": "order=scsi0",
            "onboot": 1,
        }
        return self._unwrap(client.request("POST", f"/nodes/{node}/qemu", data=data))

    # The following were "Not implemented" stubs in agent.py. Per the contract,
    # they raise ConnectorError (success=False) instead of returning a success
    # dict with an embedded error string.
    def _network_change(self, action: Action) -> Any:
        raise ConnectorError("network_change not implemented")

    def _firewall_change(self, action: Action) -> Any:
        raise ConnectorError("firewall_change not implemented")

    def _reboot_node(self, action: Action) -> Any:
        node, client = self._client_for(action)
        return self._unwrap(client.request("POST", f"/nodes/{node}/status", data={"command": "reboot"}))

    # ------------------------------------------------------------ interface
    def operations(self) -> Dict[str, OperationSpec]:
        A, N, AP = AutonomyTier.AUTO, AutonomyTier.NOTIFY, AutonomyTier.APPROVE
        specs = [
            # reads -> AUTO (idempotent)
            OperationSpec("list_nodes", self._list_nodes, A, idempotent=True),
            OperationSpec("list_vms", self._list_vms, A, idempotent=True),
            OperationSpec("list_containers", self._list_containers, A, idempotent=True),
            OperationSpec("get_vm_status", self._get_vm_status, A, idempotent=True),
            OperationSpec("get_node_metrics", self._get_node_metrics, A, idempotent=True),
            OperationSpec("get_storage_usage", self._get_storage_usage, A, idempotent=True),
            OperationSpec("list_snapshots", self._list_snapshots, A, idempotent=True),
            OperationSpec("get_cluster_status", self._get_cluster_status, A, idempotent=True),
            OperationSpec("get_network_config", self._get_network_config, A, idempotent=True),
            # safe writes -> NOTIFY
            OperationSpec("start_vm", self._start_vm, N, idempotent=True),
            OperationSpec("stop_vm", self._stop_vm, N, idempotent=True),
            OperationSpec("restart_vm", self._restart_vm, N),
            OperationSpec("create_snapshot", self._create_snapshot, N),
            # destructive / high blast radius -> APPROVE
            OperationSpec("delete_vm", self._delete_vm, AP),
            OperationSpec("delete_container", self._delete_container, AP),
            OperationSpec("delete_snapshot", self._delete_snapshot, AP),
            OperationSpec("migrate_vm", self._migrate_vm, AP),
            OperationSpec("create_vm", self._create_vm, AP),
            OperationSpec("network_change", self._network_change, AP),
            OperationSpec("firewall_change", self._firewall_change, AP),
            OperationSpec("reboot_node", self._reboot_node, AP),
        ]
        return {s.name: s for s in specs}

    def capabilities(self) -> Set[str]:
        return {"compute", "snapshot", "storage", "network", "cluster"}

    def health(self) -> HealthStatus:
        if not self._clients:
            return HealthStatus(healthy=False, detail="no proxmox nodes configured")
        # Probe the first node's cluster status as a cheap liveness check.
        node_name, client = next(iter(self._clients.items()))
        start = time.monotonic()
        res = client.request("GET", "/version")
        latency = (time.monotonic() - start) * 1000.0
        if isinstance(res, dict) and "error" in res:
            return HealthStatus(
                healthy=False, detail=f"{node_name}: {res['error']}", latency_ms=latency
            )
        return HealthStatus(healthy=True, detail=f"{node_name} reachable", latency_ms=latency)


__all__ = ["ProxmoxConnector"]
