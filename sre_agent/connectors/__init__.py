"""Connector package: hardened API clients behind a uniform interface.

Each connector exposes a set of named operations (:class:`OperationSpec`) bound
to handlers, intrinsic autonomy tiers, and idempotency flags. The
:class:`ConnectorRegistry` merges them into one operation namespace for the
executor. All HTTP goes through the hardened :class:`BaseHTTPClient` (https-only,
fail-closed TLS, bounded retries, URL-encoding, no secret leakage).
"""
from .base import Connector
from .hetzner import HetznerConnector
from .http import BaseHTTPClient
from .proxmox import ProxmoxConnector
from .registry import ConnectorRegistry, build_from_config

__all__ = [
    "Connector",
    "BaseHTTPClient",
    "ConnectorRegistry",
    "ProxmoxConnector",
    "HetznerConnector",
    "build_from_config",
]
