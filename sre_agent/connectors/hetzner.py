"""Hetzner Cloud connector.

Wraps a single Hetzner :class:`BaseHTTPClient` and exposes the Hetzner
operations that previously lived as ``_handle_hetzner_*`` methods in
``agent.py``. Integer ids are validated up front (raising
:class:`ConnectorError` on non-numeric input) per the prior review.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Set

from ..core.errors import ConnectorError
from ..core.models import Action, AutonomyTier, HealthStatus, OperationSpec
from .base import Connector
from .http import BaseHTTPClient

_DEFAULT_ENDPOINT = "https://api.hetzner.cloud/v1"


class HetznerConnector(Connector):
    """Connector for the Hetzner Cloud API."""

    name = "hetzner"

    def __init__(self, token: str, endpoint: str = _DEFAULT_ENDPOINT):
        self._client = BaseHTTPClient(
            endpoint,
            auth_header=("Authorization", f"Bearer {token}"),
            default_headers={"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _server_id(action: Action) -> int:
        """Validate and return ``server_id`` as an int.

        Raises :class:`ConnectorError` if missing or non-numeric — a bare
        ``int(...)`` on bad input would otherwise raise a ValueError.
        """
        raw = action.payload.get("server_id")
        if raw is None:
            raise ConnectorError("server_id required")
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ConnectorError(f"server_id must be an integer (got {raw!r})")

    @staticmethod
    def _check(res: Dict[str, Any]) -> Dict[str, Any]:
        """Raise on an error response, otherwise pass the dict through."""
        if isinstance(res, dict) and "error" in res:
            raise ConnectorError(str(res.get("error")))
        return res

    # --------------------------------------------------------------- read ops
    def _list_servers(self, action: Action) -> Dict[str, Any]:
        params = {
            "name": action.payload.get("name"),
            "label_selector": action.payload.get("label_selector"),
        }
        return self._check(self._client.request("GET", "/servers", params=params))

    def _get_server(self, action: Action) -> Dict[str, Any]:
        sid = self._server_id(action)
        return self._check(self._client.request("GET", f"/servers/{sid}"))

    def _list_networks(self, action: Action) -> Dict[str, Any]:
        return self._check(self._client.request("GET", "/networks"))

    def _list_volumes(self, action: Action) -> Dict[str, Any]:
        return self._check(self._client.request("GET", "/volumes"))

    def _list_firewalls(self, action: Action) -> Dict[str, Any]:
        return self._check(self._client.request("GET", "/firewalls"))

    def _list_ssh_keys(self, action: Action) -> Dict[str, Any]:
        return self._check(self._client.request("GET", "/ssh_keys"))

    # ------------------------------------------------------------- write ops
    def _create_server(self, action: Action) -> Dict[str, Any]:
        p = action.payload
        name = p.get("name")
        if not name:
            raise ConnectorError("name required")
        data: Dict[str, Any] = {
            "name": name,
            "server_type": p.get("server_type", "cx22"),
            "image": p.get("image", "ubuntu-24.04"),
        }
        if p.get("location"):
            data["location"] = p["location"]
        if p.get("ssh_keys"):
            data["ssh_keys"] = p["ssh_keys"]
        return self._check(self._client.request("POST", "/servers", data=data))

    def _delete_server(self, action: Action) -> Dict[str, Any]:
        sid = self._server_id(action)
        return self._check(self._client.request("DELETE", f"/servers/{sid}"))

    def _start_server(self, action: Action) -> Dict[str, Any]:
        sid = self._server_id(action)
        return self._check(self._client.request("POST", f"/servers/{sid}/actions/start"))

    def _stop_server(self, action: Action) -> Dict[str, Any]:
        sid = self._server_id(action)
        return self._check(self._client.request("POST", f"/servers/{sid}/actions/stop"))

    def _reboot_server(self, action: Action) -> Dict[str, Any]:
        sid = self._server_id(action)
        return self._check(self._client.request("POST", f"/servers/{sid}/actions/reboot"))

    # ------------------------------------------------------------ interface
    def operations(self) -> Dict[str, OperationSpec]:
        A, N, AP = AutonomyTier.AUTO, AutonomyTier.NOTIFY, AutonomyTier.APPROVE
        specs = [
            OperationSpec("hetzner_list_servers", self._list_servers, A, idempotent=True),
            OperationSpec("hetzner_get_server", self._get_server, A, idempotent=True),
            OperationSpec("hetzner_list_networks", self._list_networks, A, idempotent=True),
            OperationSpec("hetzner_list_volumes", self._list_volumes, A, idempotent=True),
            OperationSpec("hetzner_list_firewalls", self._list_firewalls, A, idempotent=True),
            OperationSpec("hetzner_list_ssh_keys", self._list_ssh_keys, A, idempotent=True),
            OperationSpec("hetzner_start_server", self._start_server, N, idempotent=True),
            OperationSpec("hetzner_stop_server", self._stop_server, N, idempotent=True),
            OperationSpec("hetzner_reboot_server", self._reboot_server, N),
            OperationSpec("hetzner_create_server", self._create_server, AP),
            OperationSpec("hetzner_delete_server", self._delete_server, AP),
        ]
        return {s.name: s for s in specs}

    def capabilities(self) -> Set[str]:
        return {"compute", "network", "volume", "firewall", "ssh_key"}

    def health(self) -> HealthStatus:
        start = time.monotonic()
        res = self._client.request("GET", "/server_types", params={"per_page": 1})
        latency = (time.monotonic() - start) * 1000.0
        if isinstance(res, dict) and "error" in res:
            return HealthStatus(healthy=False, detail=str(res["error"]), latency_ms=latency)
        return HealthStatus(healthy=True, detail="hetzner api reachable", latency_ms=latency)


__all__ = ["HetznerConnector"]
