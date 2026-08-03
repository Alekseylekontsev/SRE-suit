"""Observability for the SRE agent: in-process metrics + optional tracing.

- :mod:`metrics` -- SLO/uptime tracking and outcome counters (stdlib only).
- :mod:`tracing` -- OpenTelemetry spans with a no-op fallback when OTel is
  not installed.
"""

from __future__ import annotations

from .metrics import Counter, MetricsRegistry, SLOTracker
from .tracing import OTEL_AVAILABLE, get_tracer, span

__all__ = [
    "Counter",
    "SLOTracker",
    "MetricsRegistry",
    "get_tracer",
    "span",
    "OTEL_AVAILABLE",
]
