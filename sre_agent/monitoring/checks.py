"""Monitoring check primitives.

Self-contained check functions for deployed components, plus the shared
``CheckResult`` model, TLS policy, HTTP helper, and result formatting. The
orchestration that wires these together lives in ``runner.py``.

Checks availability, health, and workload metrics for:
- HTTP/HTTPS endpoints (any service with a health URL)
- LiteLLM proxy (health, models, spend, key usage)
- Proxmox VMs (status, CPU, memory, disk, uptime)
- GitLab instance (reachability, project access)
"""
from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single monitoring check."""
    name: str
    target: str
    status: str  # ok, degraded, down, error
    response_ms: float = 0
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# TLS policy for monitoring HTTP checks. Secure by default; overridden via
# configure_tls() from the loaded config (see run_platform_checks).
_TLS_VERIFY: bool = True
_TLS_CA_CERT: Optional[str] = None


def configure_tls(verify_ssl: bool = True, ca_cert: Optional[str] = None) -> None:
    """Set the TLS verification policy for subsequent monitoring checks.

    Prefer ``ca_cert`` (path to a CA bundle) to trust self-signed endpoints
    while keeping verification on; only set ``verify_ssl=False`` as an explicit
    opt-out when no CA bundle is available.
    """
    global _TLS_VERIFY, _TLS_CA_CERT
    _TLS_VERIFY = verify_ssl
    _TLS_CA_CERT = ca_cert


def _ssl_ctx() -> ssl.SSLContext:
    """Build SSL context for monitoring checks (secure by default).

    Prefers a CA bundle, then full verification against the system trust store,
    and only disables verification when explicitly configured via configure_tls.
    """
    if _TLS_CA_CERT:
        return ssl.create_default_context(cafile=_TLS_CA_CERT)
    if _TLS_VERIFY:
        return ssl.create_default_context()
    logger.warning(
        "TLS verification disabled for monitoring checks (verify_ssl=false). "
        "Set monitoring.ca_cert to a CA bundle to restore verification."
    )
    ctx = ssl.create_default_context()  # NOSONAR - intentional, config-gated opt-out
    ctx.check_hostname = False  # NOSONAR - intentional, config-gated opt-out
    ctx.verify_mode = ssl.CERT_NONE  # NOSONAR - intentional, config-gated opt-out
    return ctx


def _http_get(url: str, headers: Optional[Dict[str, str]] = None,
              timeout: int = 10) -> tuple[int, str, float]:
    """HTTP GET returning (status_code, body, elapsed_ms)."""
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            body = resp.read().decode()
            elapsed = (time.monotonic() - start) * 1000
            return resp.status, body, elapsed
    except HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        body = e.read().decode() if hasattr(e, 'read') else ""
        return e.code, body, elapsed
    except URLError as e:
        elapsed = (time.monotonic() - start) * 1000
        return 0, str(e.reason), elapsed
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return 0, str(e), elapsed


# ==================== ENDPOINT CHECKS ====================

def check_endpoint(name: str, url: str, expected_status: int = 200,
                   headers: Optional[Dict[str, str]] = None,
                   timeout: int = 10) -> CheckResult:
    """Check if an HTTP(S) endpoint is reachable and returns expected status."""
    code, body, elapsed = _http_get(url, headers=headers, timeout=timeout)
    if code == expected_status:
        status = "ok"
    elif 200 <= code < 500:
        status = "degraded"
    else:
        status = "down"

    return CheckResult(
        name=name,
        target=url,
        status=status,
        response_ms=round(elapsed, 1),
        details={"http_status": code, "body_preview": body[:200] if code != expected_status else ""},
    )


# ==================== LITELLM CHECKS ====================

def check_litellm_health(cfg: Dict[str, Any]) -> CheckResult:
    """Check LiteLLM /health/readiness endpoint."""
    url = cfg.get("url") or cfg.get("internal_url", "")
    # Prefer direct IP access, fall back to public URL
    for base in [cfg.get("internal_url"), cfg.get("url")]:
        if not base:
            continue
        target = f"{base.rstrip('/')}/health/readiness"
        code, body, elapsed = _http_get(target, timeout=10)
        if code == 200:
            try:
                data = json.loads(body)
            except ValueError:
                data = {}
            return CheckResult(
                name="litellm_health",
                target=target,
                status="ok" if data.get("status") == "healthy" else "degraded",
                response_ms=round(elapsed, 1),
                details={
                    "litellm_status": data.get("status"),
                    "db": data.get("db"),
                    "version": data.get("litellm_version"),
                },
            )
    return CheckResult(
        name="litellm_health",
        target=url,
        status="down",
        details={"error": "all endpoints unreachable"},
    )


def check_litellm_models(cfg: Dict[str, Any]) -> CheckResult:
    """Check LiteLLM /v1/models and return model count."""
    key = cfg.get("master_key", "")
    for base in [cfg.get("internal_url"), cfg.get("url")]:
        if not base:
            continue
        target = f"{base.rstrip('/')}/v1/models"
        code, body, elapsed = _http_get(
            target,
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if code == 200:
            try:
                data = json.loads(body)
                models = data.get("data", [])
            except ValueError:
                models = []
            return CheckResult(
                name="litellm_models",
                target=target,
                status="ok" if models else "degraded",
                response_ms=round(elapsed, 1),
                details={"model_count": len(models)},
            )
    return CheckResult(
        name="litellm_models",
        target=cfg.get("url", ""),
        status="down",
        details={"error": "unreachable"},
    )


def check_litellm_spend(cfg: Dict[str, Any]) -> CheckResult:
    """Check LiteLLM global spend via /global/spend/report."""
    key = cfg.get("master_key", "")
    for base in [cfg.get("internal_url"), cfg.get("url")]:
        if not base:
            continue
        target = f"{base.rstrip('/')}/global/spend/logs?limit=1"
        code, body, elapsed = _http_get(
            target,
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if code == 200:
            try:
                data = json.loads(body)
            except ValueError:
                data = []
            return CheckResult(
                name="litellm_spend",
                target=target,
                status="ok",
                response_ms=round(elapsed, 1),
                details={"recent_entries": len(data) if isinstance(data, list) else 0},
            )
    return CheckResult(
        name="litellm_spend",
        target=cfg.get("url", ""),
        status="down",
        details={"error": "unreachable"},
    )


# ==================== PROXMOX VM WORKLOAD ====================

def check_vm_workload(client, node: str, vmid: str,
                      vm_type: str = "qemu") -> CheckResult:
    """Get VM workload metrics from Proxmox (CPU, memory, disk, netin/netout, uptime)."""
    endpoint = vm_type if vm_type == "qemu" else "lxc"
    start = time.monotonic()
    result = client._request(f"/nodes/{node}/{endpoint}/{vmid}/status/current")
    elapsed = (time.monotonic() - start) * 1000

    if not isinstance(result, dict):
        result = {"error": "no/invalid response from Proxmox API"}
    data = result.get("data", result)
    if "error" in result:
        return CheckResult(
            name=f"vm_{vmid}_workload",
            target=f"{node}/{vmid}",
            status="error",
            response_ms=round(elapsed, 1),
            details={"error": result.get("error")},
        )

    vm_status = data.get("status", "unknown")
    cpu = data.get("cpu", 0)
    maxcpu = data.get("cpus", data.get("maxcpu", 1))
    mem = data.get("mem", 0)
    maxmem = data.get("maxmem", 1)
    disk = data.get("disk", 0)
    maxdisk = data.get("maxdisk", 1)
    uptime = data.get("uptime", 0)
    netin = data.get("netin", 0)
    netout = data.get("netout", 0)

    mem_pct = round(mem / maxmem * 100, 1) if maxmem else 0
    disk_pct = round(disk / maxdisk * 100, 1) if maxdisk else 0

    if vm_status != "running":
        status = "down"
    elif cpu > 0.9 or mem_pct > 95:
        status = "degraded"
    else:
        status = "ok"

    return CheckResult(
        name=f"vm_{vmid}_workload",
        target=f"{node}/{vmid}",
        status=status,
        response_ms=round(elapsed, 1),
        details={
            "vm_status": vm_status,
            "vm_name": data.get("name", ""),
            "cpu_usage": round(cpu * 100, 1),
            "cpu_count": maxcpu,
            "mem_used_mb": round(mem / 1048576),
            "mem_total_mb": round(maxmem / 1048576),
            "mem_pct": mem_pct,
            "disk_used_gb": round(disk / 1073741824, 1),
            "disk_total_gb": round(maxdisk / 1073741824, 1),
            "disk_pct": disk_pct,
            "uptime_hours": round(uptime / 3600, 1),
            "netin_mb": round(netin / 1048576, 1),
            "netout_mb": round(netout / 1048576, 1),
        },
    )


def check_node_workload(client, node: str) -> CheckResult:
    """Get Proxmox node workload metrics."""
    start = time.monotonic()
    result = client._request(f"/nodes/{node}/status")
    elapsed = (time.monotonic() - start) * 1000

    if not isinstance(result, dict):
        result = {"error": "no/invalid response from Proxmox API"}
    data = result.get("data", result)
    if "error" in result:
        return CheckResult(
            name=f"node_{node}_workload",
            target=node,
            status="error",
            response_ms=round(elapsed, 1),
            details={"error": result.get("error")},
        )

    cpu = data.get("cpu", 0)
    mem = data.get("memory", {})
    mem_used = mem.get("used", 0)
    mem_total = mem.get("total", 1)
    rootfs = data.get("rootfs", {})
    disk_used = rootfs.get("used", 0)
    disk_total = rootfs.get("total", 1)
    uptime = data.get("uptime", 0)
    loadavg = data.get("loadavg", [0, 0, 0])

    mem_pct = round(mem_used / mem_total * 100, 1) if mem_total else 0

    if cpu > 0.9 or mem_pct > 95:
        status = "degraded"
    else:
        status = "ok"

    return CheckResult(
        name=f"node_{node}_workload",
        target=node,
        status=status,
        response_ms=round(elapsed, 1),
        details={
            "cpu_usage": round(cpu * 100, 1),
            "mem_used_gb": round(mem_used / 1073741824, 1),
            "mem_total_gb": round(mem_total / 1073741824, 1),
            "mem_pct": mem_pct,
            "disk_used_gb": round(disk_used / 1073741824, 1),
            "disk_total_gb": round(disk_total / 1073741824, 1),
            "uptime_days": round(uptime / 86400, 1),
            "loadavg": loadavg,
        },
    )


# ==================== GITLAB CHECK ====================

def check_gitlab(cfg: Dict[str, Any]) -> CheckResult:
    """Check GitLab API reachability and project access."""
    url = cfg.get("url", "")
    token = cfg.get("token", "")
    project = cfg.get("project", "")

    if not url:
        return CheckResult(name="gitlab", target="", status="error",
                           details={"error": "no gitlab url configured"})

    # Check API version endpoint
    target = f"{url.rstrip('/')}/api/v4/projects/{urllib_quote(project)}"
    code, body, elapsed = _http_get(
        target,
        headers={"PRIVATE-TOKEN": token} if token else None,
        timeout=10,
    )
    if code == 200:
        try:
            data = json.loads(body)
        except ValueError:
            data = {}
        return CheckResult(
            name="gitlab",
            target=target,
            status="ok",
            response_ms=round(elapsed, 1),
            details={
                "project_name": data.get("name_with_namespace", ""),
                "default_branch": data.get("default_branch", ""),
            },
        )
    elif code == 401:
        return CheckResult(name="gitlab", target=target, status="degraded",
                           response_ms=round(elapsed, 1),
                           details={"error": "token expired or revoked", "http_status": code})
    else:
        return CheckResult(name="gitlab", target=target, status="down",
                           response_ms=round(elapsed, 1),
                           details={"http_status": code})


def urllib_quote(s: str) -> str:
    """URL-encode a string for path use."""
    import urllib.parse
    return urllib.parse.quote(s, safe="")


# ==================== RESULT FORMATTING ====================

def _format_details(details: Dict[str, Any]) -> str:
    """Render a CheckResult.details dict into a compact, truncated one-liner."""
    parts = []
    for k, v in details.items():
        if k in ("body_preview", "error") and not v:
            continue
        parts.append(f"{k}={v:.1f}" if isinstance(v, float) else f"{k}={v}")
    detail_str = ", ".join(parts)
    if len(detail_str) > 80:
        detail_str = detail_str[:77] + "..."
    return detail_str


def format_results(results: List[CheckResult], output: str = "table") -> str:
    """Format check results for display."""
    if output == "json":
        return json.dumps(
            [{"name": r.name, "target": r.target, "status": r.status,
              "response_ms": r.response_ms, "details": r.details,
              "timestamp": r.timestamp} for r in results],
            indent=2,
        )

    # Table format
    lines = []
    status_icons = {"ok": "OK", "degraded": "WARN", "down": "DOWN", "error": "ERR"}

    lines.append(f"{'CHECK':<30} {'STATUS':<8} {'RESPONSE':<10} DETAILS")
    lines.append("-" * 90)

    for r in results:
        icon = status_icons.get(r.status, "???")
        ms = f"{r.response_ms:.0f}ms" if r.response_ms else "-"
        lines.append(f"{r.name:<30} {icon:<8} {ms:<10} {_format_details(r.details)}")

    # Summary
    total = len(results)
    ok = sum(1 for r in results if r.status == "ok")
    warn = sum(1 for r in results if r.status == "degraded")
    down = sum(1 for r in results if r.status in ("down", "error"))
    lines.append("-" * 90)
    lines.append(f"Total: {total}  OK: {ok}  WARN: {warn}  DOWN: {down}")

    return "\n".join(lines)


__all__ = [
    "CheckResult",
    "configure_tls",
    "check_endpoint",
    "check_litellm_health",
    "check_litellm_models",
    "check_litellm_spend",
    "check_vm_workload",
    "check_node_workload",
    "check_gitlab",
    "urllib_quote",
    "format_results",
]
