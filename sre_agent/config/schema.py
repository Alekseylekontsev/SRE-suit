"""Typed dataclasses mirroring the SRE agent configuration structure.

Dependency-free. These dataclasses mirror ``DEFAULT_CONFIG`` from
``loader.py`` and provide a tolerant ``AgentConfig.from_dict`` constructor
(unknown keys ignored, missing keys filled from defaults) plus a ``validate``
function that returns human-readable problem strings for a raw config dict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Project
# --------------------------------------------------------------------------- #
@dataclass
class ProjectCfg:
    id: str = "1234"
    name: str = "Project_1"
    system_name_number: str = "DC6"
    naming_format: str = "<{project_id}>_{project_name}_{system_name_number}"

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ProjectCfg":
        d = d or {}
        return cls(
            id=d.get("id", cls.id),
            name=d.get("name", cls.name),
            system_name_number=d.get("system_name_number", cls.system_name_number),
            naming_format=d.get("naming_format", cls.naming_format),
        )


# --------------------------------------------------------------------------- #
# Proxmox
# --------------------------------------------------------------------------- #
@dataclass
class ProxmoxCfg:
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    inventory_on_start: bool = False
    scan_on_connect: bool = True

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ProxmoxCfg":
        d = d or {}
        return cls(
            nodes=list(d.get("nodes", []) or []),
            inventory_on_start=d.get("inventory_on_start", cls.inventory_on_start),
            scan_on_connect=d.get("scan_on_connect", cls.scan_on_connect),
        )


# --------------------------------------------------------------------------- #
# Hetzner
# --------------------------------------------------------------------------- #
@dataclass
class HetznerCfg:
    token: str = ""

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "HetznerCfg":
        d = d or {}
        return cls(token=d.get("token", cls.token))


# --------------------------------------------------------------------------- #
# Approvals (+ Slack, Gmail)
# --------------------------------------------------------------------------- #
@dataclass
class SlackCfg:
    enabled: bool = True
    webhook_url: str = ""
    channel: str = "#sre-approvals"

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SlackCfg":
        d = d or {}
        return cls(
            enabled=d.get("enabled", cls.enabled),
            webhook_url=d.get("webhook_url", cls.webhook_url),
            channel=d.get("channel", cls.channel),
        )


@dataclass
class GmailCfg:
    enabled: bool = True
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    email: str = "sre-automation@example.com"
    app_password: str = ""
    group_email: str = ""

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "GmailCfg":
        d = d or {}
        return cls(
            enabled=d.get("enabled", cls.enabled),
            smtp_server=d.get("smtp_server", cls.smtp_server),
            smtp_port=d.get("smtp_port", cls.smtp_port),
            email=d.get("email", cls.email),
            app_password=d.get("app_password", cls.app_password),
            group_email=d.get("group_email", cls.group_email),
        )


@dataclass
class ApprovalsCfg:
    primary_channel: str = "slack"  # slack | gmail | both
    batch_window_minutes: int = 180
    per_action_granularity: bool = True
    allow_batch_decline: bool = True
    slack: SlackCfg = field(default_factory=SlackCfg)
    gmail: GmailCfg = field(default_factory=GmailCfg)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ApprovalsCfg":
        d = d or {}
        return cls(
            primary_channel=d.get("primary_channel", cls.primary_channel),
            batch_window_minutes=d.get("batch_window_minutes", cls.batch_window_minutes),
            per_action_granularity=d.get("per_action_granularity", cls.per_action_granularity),
            allow_batch_decline=d.get("allow_batch_decline", cls.allow_batch_decline),
            slack=SlackCfg.from_dict(d.get("slack")),
            gmail=GmailCfg.from_dict(d.get("gmail")),
        )


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
@dataclass
class OperationsCfg:
    read_only: List[str] = field(default_factory=list)
    write_safe: List[str] = field(default_factory=list)
    approval_required: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "OperationsCfg":
        d = d or {}
        return cls(
            read_only=list(d.get("read_only", []) or []),
            write_safe=list(d.get("write_safe", []) or []),
            approval_required=list(d.get("approval_required", []) or []),
        )


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
@dataclass
class SecurityCfg:
    audit_log_location: str = "/var/log/sre_agent/audit.log"
    emergency_auto_actions: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SecurityCfg":
        d = d or {}
        return cls(
            audit_log_location=d.get("audit_log_location", cls.audit_log_location),
            emergency_auto_actions=list(d.get("emergency_auto_actions", []) or []),
        )


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #
@dataclass
class AgentConfig:
    project: ProjectCfg = field(default_factory=ProjectCfg)
    proxmox: ProxmoxCfg = field(default_factory=ProxmoxCfg)
    hetzner: HetznerCfg = field(default_factory=HetznerCfg)
    approvals: ApprovalsCfg = field(default_factory=ApprovalsCfg)
    operations: OperationsCfg = field(default_factory=OperationsCfg)
    security: SecurityCfg = field(default_factory=SecurityCfg)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "AgentConfig":
        """Build an AgentConfig from a raw dict.

        Tolerant: unknown top-level/nested keys are ignored, and missing keys
        fall back to dataclass defaults.
        """
        d = d or {}
        return cls(
            project=ProjectCfg.from_dict(d.get("project")),
            proxmox=ProxmoxCfg.from_dict(d.get("proxmox")),
            hetzner=HetznerCfg.from_dict(d.get("hetzner")),
            approvals=ApprovalsCfg.from_dict(d.get("approvals")),
            operations=OperationsCfg.from_dict(d.get("operations")),
            security=SecurityCfg.from_dict(d.get("security")),
        )


def _looks_like_production(cfg: Dict[str, Any]) -> bool:
    """Heuristic: does this config describe a production-like environment?"""
    project = cfg.get("project", {}) or {}
    obs = cfg.get("observability", {}) or {}
    haystacks = [
        str(project.get("name", "")),
        str(project.get("system_name_number", "")),
        str(cfg.get("profile", "")),
        str(cfg.get("environment", "")),
    ]
    if any("prod" in h.lower() for h in haystacks):
        return True
    # A prometheus metrics backend is a reasonable production tell.
    if str(obs.get("metrics_backend", "")).lower() == "prometheus":
        return True
    return False


def _is_placeholder(value: Any) -> bool:
    """True if a credential value is empty or an unresolved placeholder."""
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    # ${VAR}, <TOKEN>, <SLACK_WEBHOOK_URL> style placeholders are not real creds.
    if s.startswith("${") and s.endswith("}"):
        return True
    if s.startswith("<") and s.endswith(">"):
        return True
    return False


def validate(cfg: Dict[str, Any]) -> List[str]:
    """Validate a raw config dict and return human-readable problems.

    Returns an empty list when no problems are found. Checks include:
      * production-like profile with empty/placeholder Proxmox + Hetzner tokens
      * production-like profile with no Proxmox nodes
      * the same operation listed in conflicting tiers (read/write/approval)
      * an emergency auto-action that has no matching read/write/approval entry
    """
    problems: List[str] = []
    cfg = cfg or {}

    prod = _looks_like_production(cfg)

    proxmox = cfg.get("proxmox", {}) or {}
    nodes = proxmox.get("nodes", []) or []
    hetzner = cfg.get("hetzner", {}) or {}

    # Production-like config sanity checks.
    if prod:
        if not nodes:
            problems.append(
                "Production-like config has no Proxmox nodes configured "
                "(proxmox.nodes is empty)."
            )
        else:
            for i, node in enumerate(nodes):
                node = node or {}
                if _is_placeholder(node.get("token")):
                    problems.append(
                        f"Production-like config: proxmox.nodes[{i}] "
                        f"({node.get('name', 'unnamed')}) has an empty or "
                        f"placeholder token."
                    )
        if _is_placeholder(hetzner.get("token")):
            problems.append(
                "Production-like config: hetzner.token is empty or a placeholder."
            )

    # Operation-tier conflict checks.
    operations = cfg.get("operations", {}) or {}
    tiers = {
        "read_only": list(operations.get("read_only", []) or []),
        "write_safe": list(operations.get("write_safe", []) or []),
        "approval_required": list(operations.get("approval_required", []) or []),
    }
    tier_names = list(tiers.keys())
    seen: Dict[str, str] = {}
    for tier in tier_names:
        for op in tiers[tier]:
            if op in seen and seen[op] != tier:
                problems.append(
                    f"Operation {op!r} is listed in conflicting tiers: "
                    f"{seen[op]!r} and {tier!r}."
                )
            else:
                seen[op] = tier

    # Emergency auto-actions should map to a known operation tier.
    security = cfg.get("security", {}) or {}
    known_ops = set(seen.keys())
    for op in security.get("emergency_auto_actions", []) or []:
        if op not in known_ops:
            problems.append(
                f"Emergency auto-action {op!r} has no matching read_only, "
                f"write_safe, or approval_required operation entry."
            )

    return problems


__all__ = [
    "ProjectCfg",
    "ProxmoxCfg",
    "HetznerCfg",
    "SlackCfg",
    "GmailCfg",
    "ApprovalsCfg",
    "OperationsCfg",
    "SecurityCfg",
    "AgentConfig",
    "validate",
]
