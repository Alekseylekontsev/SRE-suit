"""Monitoring orchestration.

Wires the check primitives in ``checks.py`` into a full platform pass, with
per-check exception isolation so a single failing node/VM never aborts the
whole run. Exposes both the functional ``run_platform_checks`` entry point and
a thin ``MonitorRunner`` class wrapper.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sre_agent.core.models import HealthStatus
from sre_agent.monitoring import checks as _checks
from sre_agent.monitoring.checks import (
    CheckResult,
    check_endpoint,
    check_gitlab,
    check_litellm_health,
    check_litellm_models,
    check_litellm_spend,
    configure_tls,
    format_results,
)

logger = logging.getLogger(__name__)


# ==================== FULL PLATFORM CHECK ====================

def run_platform_checks(config: Dict[str, Any],
                        proxmox_clients: Optional[Dict] = None,
                        ) -> List[CheckResult]:
    """Run all configured monitoring checks and return results."""
    results: List[CheckResult] = []

    # Apply TLS policy from config (secure by default).
    mon_cfg = config.get("monitoring", {})
    configure_tls(
        verify_ssl=mon_cfg.get("verify_ssl", True),
        ca_cert=mon_cfg.get("ca_cert"),
    )

    # LiteLLM
    litellm_cfg = config.get("litellm", {})
    if litellm_cfg.get("url") or litellm_cfg.get("internal_url"):
        results.append(check_litellm_health(litellm_cfg))
        results.append(check_litellm_models(litellm_cfg))
        results.append(check_litellm_spend(litellm_cfg))

    # GitLab
    gitlab_cfg = config.get("gitlab", {})
    if gitlab_cfg.get("url"):
        results.append(check_gitlab(gitlab_cfg))

    # Custom endpoints
    for ep in config.get("monitoring", {}).get("endpoints", []):
        results.append(check_endpoint(
            name=ep.get("name", ep.get("url", "unknown")),
            url=ep["url"],
            expected_status=ep.get("expected_status", 200),
            headers=ep.get("headers"),
            timeout=ep.get("timeout", 10),
        ))

    # Proxmox node + monitored VMs
    if proxmox_clients:
        results.extend(_proxmox_checks(config, proxmox_clients, litellm_cfg))

    return results


def _proxmox_checks(config: Dict[str, Any], proxmox_clients: Dict,
                    litellm_cfg: Dict[str, Any]) -> List[CheckResult]:
    """Node + monitored-VM workload checks for all Proxmox clients."""
    results: List[CheckResult] = []

    def _safe(name: str, target: str, fn) -> CheckResult:
        """Run a check, converting any unexpected error into an 'error' result
        so a single failing node/VM never aborts the whole monitoring pass."""
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - intentional per-check isolation
            logger.warning("Monitoring check %s (%s) raised: %s", name, target, e)
            return CheckResult(name=name, target=target, status="error",
                               details={"error": str(e)})

    # Resolve check functions via the checks module (not by direct import) so
    # that test patches on ``sre_agent.monitor.check_*`` -- which alias the
    # checks module -- take effect here.
    for node_name, client in proxmox_clients.items():
        results.append(_safe(
            f"node_{node_name}_workload", node_name,
            lambda c=client, n=node_name: _checks.check_node_workload(c, n),
        ))

    for vm in config.get("monitoring", {}).get("vms", []):
        node = vm.get("node")
        if node and node in proxmox_clients:
            vmid = str(vm.get("vmid"))
            results.append(_safe(
                f"vm_{vmid}_workload", f"{node}/{vmid}",
                lambda n=node, v=vmid, t=vm.get("type", "qemu"): _checks.check_vm_workload(
                    proxmox_clients[n], n, v, t),
            ))

    # Also check LiteLLM VM if configured
    llm_node = litellm_cfg.get("proxmox_node")
    if llm_node and litellm_cfg.get("proxmox_vmid") and llm_node in proxmox_clients:
        llm_vmid = str(litellm_cfg["proxmox_vmid"])
        results.append(_safe(
            f"vm_{llm_vmid}_workload", f"{llm_node}/{llm_vmid}",
            lambda n=llm_node, v=llm_vmid: _checks.check_vm_workload(proxmox_clients[n], n, v),
        ))

    return results


def health_from_check(result: CheckResult) -> HealthStatus:
    """Project a CheckResult onto the shared HealthStatus contract.

    A future SLO tracker can consume this; "ok"/"degraded" are considered
    healthy (the target is serving), while "down"/"error" are not. Not wired
    into the platform pass yet.
    """
    return HealthStatus(
        healthy=result.status in ("ok", "degraded"),
        detail=result.status,
        latency_ms=result.response_ms or None,
    )


class MonitorRunner:
    """Thin object wrapper over the functional monitoring entry points.

    Holds the loaded config (and optional Proxmox clients) so callers can run
    repeated passes without re-threading arguments.
    """

    def __init__(self, config: Dict[str, Any],
                 proxmox_clients: Optional[Dict] = None) -> None:
        self.config = config
        self.proxmox_clients = proxmox_clients

    def run(self, proxmox_clients: Optional[Dict] = None) -> List[CheckResult]:
        """Run a full platform pass. ``proxmox_clients`` overrides the instance
        default for this call only."""
        clients = proxmox_clients if proxmox_clients is not None else self.proxmox_clients
        return run_platform_checks(self.config, clients)

    @staticmethod
    def format(results: List[CheckResult], output: str = "table") -> str:
        """Format check results for display (table or json)."""
        return format_results(results, output)


__all__ = [
    "run_platform_checks",
    "_proxmox_checks",
    "health_from_check",
    "MonitorRunner",
    "HealthStatus",
]
