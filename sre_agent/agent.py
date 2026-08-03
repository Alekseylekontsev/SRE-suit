"""Backward-compatible facade for the pre-coordinator SREAgent API.

New integrations should use :class:`sre_agent.coordinator.Coordinator`.  This
module remains available for existing callers that register or invoke legacy
action handlers directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

from .core.models import Action


class SREAgent:
    """Compatibility layer for legacy ``SREAgent`` consumers."""

    _PROXMOX_METHODS = {
        "get_vm_status": "get_vm_status",
        "get_node_metrics": "get_node_status",
        "list_snapshots": "list_snapshots",
        "get_network_config": "get_network_config",
        "start_vm": "start_vm",
        "stop_vm": "stop_vm",
        "restart_vm": "restart_vm",
        "create_snapshot": "create_snapshot",
        "delete_vm": "delete_vm",
        "delete_snapshot": "delete_snapshot",
        "delete_container": "delete_container",
        "create_vm": "create_vm",
    }
    _HETZNER_ID_METHODS = {
        "hetzner_get_server": "get_server",
        "hetzner_delete_server": "delete_server",
        "hetzner_start_server": "start_server",
        "hetzner_stop_server": "stop_server",
        "hetzner_reboot_server": "reboot_server",
    }
    _HETZNER_METHODS = {
        "hetzner_list_servers": "list_servers",
        "hetzner_create_server": "create_server",
        "hetzner_list_networks": "list_networks",
        "hetzner_list_volumes": "list_volumes",
        "hetzner_list_firewalls": "list_firewalls",
        "hetzner_list_ssh_keys": "list_ssh_keys",
    }
    _STUBS = {
        "list_backups",
        "resize_vm",
        "mount_iso",
        "network_change",
        "firewall_change",
        "delete_backup",
        "stop_production_vm",
        "migrate_vm",
        "reboot_node",
        "storage_config",
        "create_container",
        "modify_vm_config",
    }

    def __init__(self, config: Dict[str, Any] | None = None, config_path: str | None = None):
        self.config = config or {}
        self.config_path = config_path
        self.proxmox_clients: Dict[str, Any] = {}
        self.hetzner: Any = None
        self._audit_entries: list[Dict[str, Any]] = []
        self._action_handlers: Dict[str, Callable[[Action], Any]] = {
            "list_nodes": self._list_nodes,
            "list_vms": self._list_vms,
            "list_containers": self._list_containers,
            "get_storage_usage": self._get_storage_usage,
            "get_cluster_status": self._get_cluster_status,
            "attach_volume": self._attach_volume,
            "detach_volume": self._detach_volume,
        }
        self._action_handlers.update(
            {name: self._proxmox_handler(method) for name, method in self._PROXMOX_METHODS.items()}
        )
        self._action_handlers.update(
            {name: self._hetzner_id_handler(method) for name, method in self._HETZNER_ID_METHODS.items()}
        )
        self._action_handlers.update(
            {name: self._hetzner_handler(method) for name, method in self._HETZNER_METHODS.items()}
        )
        self._action_handlers.update({name: self._not_implemented(name) for name in self._STUBS})

    @staticmethod
    def _call(client: Any, method: str, **payload: Any) -> Any:
        return getattr(client, method)(**payload)

    def _client(self, action: Action) -> Any:
        node = action.payload.get("node")
        client = self.proxmox_clients.get(node)
        if client is None:
            return {"error": f"Unknown node: {node}"}
        return client

    def _proxmox_handler(self, method: str) -> Callable[[Action], Any]:
        def handler(action: Action) -> Any:
            client = self._client(action)
            if isinstance(client, dict):
                return client
            payload = {k: v for k, v in action.payload.items() if k != "node"}
            return self._call(client, method, **payload)

        return handler

    def _list_nodes(self, action: Action) -> list[str]:
        return list(self.proxmox_clients)

    def _all_clients(self, method: str) -> list[Any]:
        return [getattr(client, method)() for client in self.proxmox_clients.values()]

    def _list_vms(self, action: Action) -> list[Any]:
        return self._all_clients("list_vms")

    def _list_containers(self, action: Action) -> list[Any]:
        return self._all_clients("list_containers")

    def _get_cluster_status(self, action: Action) -> list[Any]:
        return self._all_clients("get_cluster_status")

    def _get_storage_usage(self, action: Action) -> Any:
        client = self._client(action)
        if isinstance(client, dict):
            return client
        result = []
        for item in client.list_storage():
            storage = item.get("storage", item.get("id"))
            result.append(client.get_storage_status(storage))
        return result

    def _require_hetzner(self) -> Any:
        if self.hetzner is None:
            return {"error": "Hetzner client is not configured"}
        return self.hetzner

    def _hetzner_id_handler(self, method: str) -> Callable[[Action], Any]:
        def handler(action: Action) -> Any:
            raw = action.payload.get("server_id")
            if raw is None:
                return {"error": "server_id required"}
            client = self._require_hetzner()
            if isinstance(client, dict):
                return client
            return getattr(client, method)(int(raw))

        return handler

    def _hetzner_handler(self, method: str) -> Callable[[Action], Any]:
        def handler(action: Action) -> Any:
            client = self._require_hetzner()
            if isinstance(client, dict):
                return client
            return getattr(client, method)(**action.payload)

        return handler

    def _attach_volume(self, action: Action) -> Any:
        volume_id, server_id = action.payload.get("volume_id"), action.payload.get("server_id")
        if volume_id is None or server_id is None:
            return {"error": "volume_id and server_id required"}
        return self.hetzner.attach_volume(int(volume_id), int(server_id))

    def _detach_volume(self, action: Action) -> Any:
        volume_id = action.payload.get("volume_id")
        if volume_id is None:
            return {"error": "volume_id required"}
        return self.hetzner.detach_volume(int(volume_id))

    @staticmethod
    def _not_implemented(name: str) -> Callable[[Action], Dict[str, str]]:
        return lambda action: {"error": f"{name} not implemented"}

    def _audit(self, event: str, data: Dict[str, Any] | None = None) -> None:
        self._audit_entries.append(
            {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, "data": data or {}}
        )

    def get_audit_log(self) -> list[Dict[str, Any]]:
        return list(self._audit_entries)

    def export_audit_log(self, path: str) -> None:
        Path(path).write_text(json.dumps(self._audit_entries, indent=2), encoding="utf-8")


__all__ = ["SREAgent"]
