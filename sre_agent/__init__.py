"""SRE Agent — a modular, environment-agnostic infrastructure coordinator.

A deterministic control plane (config → secrets → connectors → hooks →
classifier → executor → approvals → audit) with pluggable connectors, skills,
change control, a data plane, and an optional LLM reasoning layer. The CLI
entry point is :mod:`sre_agent.cli`; the orchestrator is
:class:`sre_agent.coordinator.Coordinator`.
"""

__all__ = [
    "coordinator", "cli",
    "core", "config", "connectors", "secrets", "hooks", "approvals",
    "monitoring", "skills", "reasoning", "observability",
    "changecontrol", "dataplane", "naming",
]
