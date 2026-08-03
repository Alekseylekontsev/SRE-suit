"""Configuration loader for the universal SRE agent (typed package edition).

Ported VERBATIM in behavior from the legacy single-file ``sre_agent/config.py``:
dependency-free, JSON/YAML support, environment variable overrides, deepcopy
defaults, secure (0600) save, and package-relative profile resolution.

Two improvements over the legacy loader:
  * Profile files are resolved against a configurable ``profiles_dir`` (defaults
    to ``sre_agent/configuration/profiles``), with a fallback to the legacy
    ``sre_agent/`` directory so existing ``config.<profile>.yaml`` files still
    load until the integrator moves them.
  * Numeric env overrides (BATCH_WINDOW, smtp_port) are coerced to ``int`` with
    a try/except instead of being injected as raw strings.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


# Directory bundled with this package for profile config files.
_PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
# Legacy location (sre_agent/) where config.<profile>.yaml files currently live.
_LEGACY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Env vars whose values must be coerced to int (legacy injected raw strings).
_INT_ENV_PATHS = {
    ("approvals", "batch_window_minutes"),
    ("approvals", "gmail", "smtp_port"),
}


DEFAULT_CONFIG: Dict[str, Any] = {
    "project": {
        "id": "1234",
        "name": "Project_1",
        "system_name_number": "DC6",
        "naming_format": "<{project_id}>_{project_name}_{system_name_number}",
    },
    "proxmox": {
        "nodes": [],  # Filled at runtime or via inventory discovery
        "inventory_on_start": False,
        "scan_on_connect": True,
    },
    "networks": {
        "external_ip": None,
        "vlan_config": {},  # {} mapping of vlan_id -> subnet
    },
    "hetzner": {
        "token": "",
    },
    "observability": {
        "metrics_backend": "none",  # or "prometheus"
        "prometheus_url": "",
    },
    "gitlab": {
        "url": "https://gitlab.example.com",
        "project": "admins/hetzner_proxmox",
        "token": "",
        "tf_backend": "local",
    },
    "approvals": {
        "primary_channel": "slack",  # slack | gmail | both
        "batch_window_minutes": 180,  # 3 hours
        "per_action_granularity": True,
        "allow_batch_decline": True,
        # Allow-list of identities permitted to approve. FAIL-CLOSED: empty means
        # no one can approve until operators are configured here.
        "authorized_approvers": [],
        # Separation of duties: an approver cannot approve their own request
        # unless this is explicitly enabled.
        "allow_self_approval": False,
        "slack": {
            "enabled": True,
            "webhook_url": "",
            "channel": "#sre-approvals",
        },
        "gmail": {
            "enabled": True,
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
            "email": "sre-automation@example.com",
            "app_password": "",
            "group_email": "",
        },
    },
    "operations": {
        "read_only": [
            "list_nodes",
            "list_vms",
            "list_containers",
            "get_vm_status",
            "get_node_metrics",
            "get_storage_usage",
            "list_snapshots",
            "list_backups",
            "get_cluster_status",
            "get_network_config",
            "hetzner_list_servers",
            "hetzner_get_server",
            "hetzner_list_networks",
            "hetzner_list_volumes",
            "hetzner_list_firewalls",
            "hetzner_list_ssh_keys",
            "monitor_platform",
            "monitor_vm",
            "monitor_litellm",
            "monitor_gitlab",
        ],
        "write_safe": [
            "start_vm",
            "stop_vm",
            "restart_vm",
            "create_snapshot",
            "resize_vm",
            "mount_iso",
            "attach_volume",
            "detach_volume",
            "hetzner_start_server",
            "hetzner_stop_server",
            "hetzner_reboot_server",
        ],
        "approval_required": [
            "delete_vm",
            "delete_container",
            "delete_snapshot",
            "network_change",
            "firewall_change",
            "delete_backup",
            "stop_production_vm",
            "migrate_vm",
            "reboot_node",
            "storage_config",
            "create_vm",
            "create_container",
            "modify_vm_config",
            "hetzner_create_server",
            "hetzner_delete_server",
        ],
    },
    "security": {
        "audit_log_location": "/var/log/sre_agent/audit.log",
        "emergency_auto_actions": [
            "delete_old_backups",
            "stop_non_production_vm",
            "delete_temp_files",
        ],
    },
    "discovery": {
        "enabled": False,
        "schedule": "@daily",
        "network_scan": True,
    },
}


def load_config(
    path: Optional[str] = None,
    env_prefix: str = "SRE_",
    profile: Optional[str] = None,
    profiles_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Load configuration from file (JSON/YAML) or environment variables.

    Args:
        path: Path to config file (JSON or YAML). If None, auto-detects from env.
        env_prefix: Prefix for environment variable overrides (default: "SRE_")
        profile: Environment profile name (e.g., "production", "staging")
        profiles_dir: Directory holding ``config.<profile>.{yaml,yml,json}``
            files. Defaults to the bundled ``configuration/profiles`` dir; the
            legacy ``sre_agent/`` dir is always tried as a fallback.

    Precedence:
        1. Environment variables (highest)
        2. Config file (JSON/YAML)
        3. Profile-specific config (if specified)
        4. Defaults (lowest)
    """
    cfg: Dict[str, Any] = {}

    # 1. Load base config from file
    if path and os.path.exists(path):
        cfg = _load_file(path)
    else:
        cfg = _autodetect_config()

    # 2. Load profile-specific config. The bundled config.<profile>.{yaml,yml,json}
    #    files live alongside the package, so resolve relative to the package dir
    #    (not the caller's CWD) — otherwise `sre-agent --profile production` run
    #    from elsewhere silently loads no credentials and falls back to defaults.
    #    We search the configured profiles_dir first, then fall back to the legacy
    #    sre_agent/ directory so the existing yaml files keep loading.
    if profile and profile != "default":
        search_dirs = []
        for d in (profiles_dir, _PROFILES_DIR, _LEGACY_DIR):
            if d and d not in search_dirs:
                search_dirs.append(d)

        found = False
        for d in search_dirs:
            for ext in (".yaml", ".yml", ".json"):
                profile_path = os.path.join(d, f"config.{profile}{ext}")
                if os.path.exists(profile_path):
                    cfg = _merge_config(cfg, _load_file(profile_path))
                    found = True
                    break
            if found:
                break
        if not found:
            logger.warning(
                "Profile %r requested but no config.%s.{yaml,yml,json} found in %s; "
                "using base/default config.", profile, profile, search_dirs,
            )

    # 3. Apply environment variable overrides
    cfg = _apply_env_overrides(cfg, env_prefix)

    # 4. Ensure defaults for missing keys
    cfg = _ensure_defaults(cfg)

    return cfg


def _autodetect_config() -> Dict[str, Any]:
    """Load the first config file matching known base names and extensions."""
    for base in ["config", "sre_config"]:
        for ext in [".yaml", ".yml", ".json"]:
            auto_path = f"{base}{ext}"
            if os.path.exists(auto_path):
                return _load_file(auto_path)
    return {}


def _load_file(path: str) -> Dict[str, Any]:
    """Load config from JSON or YAML file."""
    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8") as f:
        if ext in (".yaml", ".yml"):
            if YAML_AVAILABLE:
                return yaml.safe_load(f) or {}
            else:
                raise ImportError("PyYAML not installed. Install with: pip install pyyaml")
        else:
            return json.load(f)


def _merge_config(base: Dict, override: Dict) -> Dict:
    """Deep merge override config into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(cfg: Dict[str, Any], prefix: str = "SRE_") -> Dict[str, Any]:
    """Apply environment variable overrides to config.

    Example:
        SRE_PROXMOX_NODES_0_HOST=192.168.1.1 -> cfg["proxmox"]["nodes"][0]["host"]
        SRE_APPROVALS_PRIMARY_CHANNEL=slack -> cfg["approvals"]["primary_channel"]
    """
    result = cfg.copy()

    # Map of env vars to config paths
    env_mappings = {
        f"{prefix}PROJECT_ID": ("project", "id"),
        f"{prefix}PROJECT_NAME": ("project", "name"),
        f"{prefix}SYSTEM_NAME": ("project", "system_name_number"),
        f"{prefix}HETZNER_TOKEN": ("hetzner", "token"),
        f"{prefix}PROXMOX_HOST": ("proxmox", "nodes", 0, "host"),
        f"{prefix}PROXMOX_TOKEN": ("proxmox", "nodes", 0, "token"),
        f"{prefix}SLACK_WEBHOOK": ("approvals", "slack", "webhook_url"),
        f"{prefix}SLACK_CHANNEL": ("approvals", "slack", "channel"),
        f"{prefix}GMAIL_EMAIL": ("approvals", "gmail", "email"),
        f"{prefix}GMAIL_PASSWORD": ("approvals", "gmail", "app_password"),
        f"{prefix}GMAIL_GROUP": ("approvals", "gmail", "group_email"),
        f"{prefix}GMAIL_SMTP_PORT": ("approvals", "gmail", "smtp_port"),
        f"{prefix}APPROVAL_CHANNEL": ("approvals", "primary_channel"),
        f"{prefix}BATCH_WINDOW": ("approvals", "batch_window_minutes"),
        f"{prefix}METRICS_BACKEND": ("observability", "metrics_backend"),
        f"{prefix}PROMETHEUS_URL": ("observability", "prometheus_url"),
        f"{prefix}GITLAB_URL": ("gitlab", "url"),
        f"{prefix}GITLAB_PROJECT": ("gitlab", "project"),
        f"{prefix}GITLAB_TOKEN": ("gitlab", "token"),
        f"{prefix}EXTERNAL_IP": ("networks", "external_ip"),
    }

    for env_var, path in env_mappings.items():
        value = os.environ.get(env_var)
        if value is not None:
            # Coerce numeric keys (BATCH_WINDOW, smtp_port) to int. The legacy
            # loader injected the raw string here; keep the env value as a string
            # if it does not parse as an int so we never crash on bad input.
            if path in _INT_ENV_PATHS:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    logger.warning(
                        "Ignoring non-integer %s=%r; expected an int for %s",
                        env_var, value, ".".join(str(p) for p in path),
                    )
                    continue
            _set_nested(result, path, value)

    # Handle raw JSON blob
    raw_json = os.environ.get(f"{prefix}CONFIG_JSON")
    if raw_json:
        try:
            json_cfg = json.loads(raw_json)
            result = _merge_config(result, json_cfg)
        except json.JSONDecodeError as e:
            logger.warning("Ignoring invalid %sCONFIG_JSON: %s", prefix, e)

    return result


def _set_nested(d: Dict, path: tuple, value: Any) -> None:
    """Set a nested dictionary value from a path tuple."""
    current = d
    for key in path[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[path[-1]] = value


def _ensure_defaults(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure all required keys exist with default values."""
    # Deep copy so callers that mutate nested lists/dicts (e.g. operations lists)
    # cannot corrupt the module-global DEFAULT_CONFIG for the rest of the process.
    result = copy.deepcopy(DEFAULT_CONFIG)
    result = _merge_config(result, cfg)
    return result


def save_config(cfg: Dict[str, Any], path: str) -> None:
    """Persist config as JSON to path (0600 — it may contain secrets)."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # Create with restrictive permissions before writing any credentials.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_proxmox_nodes(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Get list of Proxmox nodes from config."""
    return cfg.get("proxmox", {}).get("nodes", [])


def get_approval_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Get approval configuration."""
    return cfg.get("approvals", {})


def get_project_info(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Get project identification info."""
    return cfg.get("project", {})


__all__ = [
    "DEFAULT_CONFIG",
    "load_config",
    "save_config",
    "get_proxmox_nodes",
    "get_approval_config",
    "get_project_info",
]
