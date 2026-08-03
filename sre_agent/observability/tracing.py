"""Optional OpenTelemetry tracing with a transparent no-op fallback.

OpenTelemetry is an optional dependency. If ``opentelemetry`` is not
installed, this module degrades to no-op spans so callers can use the same
API unconditionally:

    from sre_agent.observability import tracing
    with tracing.get_tracer().span("do_thing", target="vm-130") as span:
        span.set_attribute("result", "ok")

The public surface is intentionally tiny: ``get_tracer``, ``span`` (a
module-default shortcut), and the ``OTEL_AVAILABLE`` flag.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

__all__ = ["get_tracer", "span", "OTEL_AVAILABLE"]

try:  # pragma: no cover - exercised only when otel is installed
    from opentelemetry import trace as _otel_trace

    OTEL_AVAILABLE = True
except Exception:  # ImportError, or partial/broken install
    _otel_trace = None
    OTEL_AVAILABLE = False


class _NoopSpan:
    """A span that records nothing. Usable as a context manager."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401
        return None

    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False  # do not suppress exceptions


class _NoopTracer:
    """Tracer that yields :class:`_NoopSpan` objects."""

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


class _OtelTracer:
    """Thin wrapper adapting an OpenTelemetry tracer to the ``span`` API."""

    def __init__(self, name: str) -> None:
        self._tracer = _otel_trace.get_tracer(name)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Any]:
        with self._tracer.start_as_current_span(name) as otel_span:
            for key, value in attributes.items():
                otel_span.set_attribute(key, value)
            yield otel_span


def get_tracer(name: str = "sre_agent") -> Any:
    """Return a tracer exposing a ``span(name, **attributes)`` context manager.

    Returns a real OTel-backed tracer when available, otherwise a no-op
    tracer. Either way, ``get_tracer().span(...)`` works.
    """
    if OTEL_AVAILABLE:
        return _OtelTracer(name)
    return _NoopTracer()


_DEFAULT_TRACER = get_tracer()


def span(name: str, **attributes: Any):
    """Module-level shortcut: ``span(...)`` on a default tracer."""
    return _DEFAULT_TRACER.span(name, **attributes)
