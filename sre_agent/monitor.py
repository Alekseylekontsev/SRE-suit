"""Backwards-compatible shim for the monitoring package.

The monitor implementation moved to ``sre_agent.monitoring`` (split into
``checks`` for the primitives and ``runner`` for orchestration). This module is
kept so existing imports and test patch targets keep working:

    from sre_agent import monitor
    monitor.check_endpoint(...)
    patch("sre_agent.monitor._http_get", ...)
    patch("sre_agent.monitor.ssl.create_default_context", ...)

To make patching ``sre_agent.monitor._http_get`` / ``sre_agent.monitor.ssl``
affect the check functions (which look those names up in their *defining*
module), this shim aliases itself to the ``checks`` module object in
``sys.modules``: ``sre_agent.monitor`` and ``sre_agent.monitoring.checks``
become the same module, so a patch on either name hits the same globals the
check functions read. Runner-level names are then attached onto that shared
module object.
"""
from __future__ import annotations

import sys

from sre_agent.monitoring import checks as _checks

# Pull every public + patched name from checks (CheckResult, configure_tls,
# _ssl_ctx, _http_get, ssl, check_*, format_results, _format_details, ...).
from sre_agent.monitoring.checks import *  # noqa: F401,F403
from sre_agent.monitoring.checks import (  # noqa: F401 - explicit names tests rely on
    CheckResult,
    _format_details,
    _http_get,
    _ssl_ctx,
    check_endpoint,
    check_node_workload,
    check_vm_workload,
    configure_tls,
    format_results,
    ssl,
)
from sre_agent.monitoring.runner import (  # noqa: F401
    MonitorRunner,
    _proxmox_checks,  # noqa: F401
    health_from_check,
    run_platform_checks,
)

# Alias this module to the checks module object so that patching
# ``sre_agent.monitor._http_get`` (or ``sre_agent.monitor.ssl``) rebinds the
# exact globals the check functions resolve against. We copy the runner-level
# names onto the checks module first so they remain importable via
# ``sre_agent.monitor`` after the alias.
_checks.run_platform_checks = run_platform_checks
_checks._proxmox_checks = _proxmox_checks
_checks.MonitorRunner = MonitorRunner
_checks.health_from_check = health_from_check

sys.modules[__name__] = _checks
