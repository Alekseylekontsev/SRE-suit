"""Typed configuration package for the SRE agent.

Public API:
    load_config, save_config, DEFAULT_CONFIG  -- the loader
    AgentConfig, validate                      -- the typed schema layer
    get_proxmox_nodes / get_approval_config / get_project_info -- accessors

The loader internals (``_merge_config``, ``_ensure_defaults``,
``_autodetect_config``) are also re-exported so existing call sites and tests
can import them directly from ``sre_agent.config``.
"""
from __future__ import annotations

from .loader import (
    DEFAULT_CONFIG,
    _autodetect_config,
    _ensure_defaults,
    _merge_config,
    get_approval_config,
    get_project_info,
    get_proxmox_nodes,
    load_config,
    save_config,
)
from .schema import AgentConfig, validate

__all__ = [
    "load_config",
    "save_config",
    "DEFAULT_CONFIG",
    "AgentConfig",
    "validate",
    "get_proxmox_nodes",
    "get_approval_config",
    "get_project_info",
    "_merge_config",
    "_ensure_defaults",
    "_autodetect_config",
]
