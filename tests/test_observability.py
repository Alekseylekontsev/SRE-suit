"""Tests for sre_agent.observability (metrics + tracing).

Run (no opentelemetry installed):
    python3 -m pytest tests/test_observability.py -q
"""

from __future__ import annotations

import math

from sre_agent.observability import (
    OTEL_AVAILABLE,
    Counter,
    MetricsRegistry,
    SLOTracker,
    get_tracer,
    span,
)
from sre_agent.observability.tracing import _NoopSpan, _NoopTracer


# --------------------------------------------------------------------------- #
# Counter
# --------------------------------------------------------------------------- #
def test_counter_inc_value_no_labels():
    c = Counter("requests")
    assert c.value() == 0
    c.inc()
    c.inc(amount=2)
    assert c.value() == 3


def test_counter_inc_value_with_labels():
    c = Counter("actions")
    c.inc(operation="reboot")
    c.inc(operation="reboot")
    c.inc(operation="snapshot")
    assert c.value(operation="reboot") == 2
    assert c.value(operation="snapshot") == 1
    assert c.value(operation="missing") == 0


def test_counter_snapshot():
    c = Counter("actions")
    c.inc(operation="reboot")
    c.inc(operation="snapshot", amount=3)
    snap = c.snapshot()
    assert snap[(("operation", "reboot"),)] == 1
    assert snap[(("operation", "snapshot"),)] == 3
    # snapshot is a copy: mutating it does not affect the counter
    snap.clear()
    assert c.value(operation="reboot") == 1


def test_counter_negative_increment_rejected():
    c = Counter("x")
    try:
        c.inc(amount=-1)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError on negative increment")


# --------------------------------------------------------------------------- #
# SLOTracker
# --------------------------------------------------------------------------- #
def test_slo_all_healthy():
    slo = SLOTracker()
    for i in range(10):
        slo.record("api", healthy=True, ts=float(i))
    assert slo.availability("api") == 1.0
    assert math.isclose(slo.error_budget_remaining("api"), 1.0)


def test_slo_exactly_at_objective():
    slo = SLOTracker()
    # 9999 healthy, 1 down -> availability 0.9999, exactly the objective
    for i in range(9999):
        slo.record("api", healthy=True, ts=float(i))
    slo.record("api", healthy=False, ts=9999.0)
    assert math.isclose(slo.availability("api"), 0.9999, rel_tol=1e-9)
    # budget consumed exactly -> ~0.0
    assert math.isclose(
        slo.error_budget_remaining("api", objective=0.9999), 0.0, abs_tol=1e-6
    )


def test_slo_budget_exhausted_negative():
    slo = SLOTracker()
    # 9998 healthy, 2 down -> twice the allowed downtime -> negative budget
    for i in range(9998):
        slo.record("api", healthy=True, ts=float(i))
    slo.record("api", healthy=False, ts=9998.0)
    slo.record("api", healthy=False, ts=9999.0)
    assert math.isclose(slo.availability("api"), 0.9998, rel_tol=1e-9)
    budget = slo.error_budget_remaining("api", objective=0.9999)
    assert budget < 0
    assert math.isclose(budget, -1.0, abs_tol=1e-6)


def test_slo_summary():
    slo = SLOTracker()
    slo.record("db", healthy=True, ts=1.0)
    slo.record("db", healthy=False, ts=2.0)
    summary = slo.summary()
    assert summary["db"]["observations"] == 2
    assert summary["db"]["healthy"] == 1
    assert math.isclose(summary["db"]["availability"], 0.5)


def test_slo_unknown_target():
    slo = SLOTracker()
    assert slo.availability("nope") == 0.0


# --------------------------------------------------------------------------- #
# MetricsRegistry
# --------------------------------------------------------------------------- #
def test_registry_record_action_totals_and_failures():
    reg = MetricsRegistry()
    reg.record_action("reboot", success=True)
    reg.record_action("reboot", success=False)
    reg.record_action("snapshot", success=True)

    total = reg.counter("actions_total")
    failed = reg.counter("actions_failed")
    assert total.value(operation="reboot") == 2
    assert total.value(operation="snapshot") == 1
    assert failed.value(operation="reboot") == 1
    assert failed.value(operation="snapshot") == 0


def test_registry_counter_is_stable():
    reg = MetricsRegistry()
    a = reg.counter("c")
    b = reg.counter("c")
    assert a is b


def test_registry_render_prometheus_contains_names():
    reg = MetricsRegistry()
    reg.record_action("reboot", success=False)
    text = reg.render_prometheus()
    assert "actions_total" in text
    assert "actions_failed" in text
    assert 'operation="reboot"' in text
    assert "# TYPE actions_total counter" in text


def test_registry_snapshot():
    reg = MetricsRegistry()
    reg.record_action("reboot", success=True)
    reg.slo.record("api", healthy=True, ts=1.0)
    snap = reg.snapshot()
    assert "counters" in snap
    assert "slo" in snap
    assert "actions_total" in snap["counters"]
    assert snap["slo"]["api"]["observations"] == 1


# --------------------------------------------------------------------------- #
# tracing
# --------------------------------------------------------------------------- #
def test_otel_available_flag_is_bool():
    # The flag mirrors whether opentelemetry could be imported. We do not
    # hardcode its value so the suite passes whether or not OTel is present.
    assert isinstance(OTEL_AVAILABLE, bool)


def test_tracer_span_is_context_manager():
    tracer = get_tracer()
    with tracer.span("x", k="v") as s:
        s.set_attribute("another", 123)
    # entering/exiting again still works
    with tracer.span("x"):
        pass


def test_noop_tracer_and_span_directly():
    # Exercise the no-op fallback path explicitly, regardless of whether
    # opentelemetry happens to be installed in this environment.
    tracer = _NoopTracer()
    with tracer.span("x", k="v") as s:
        assert isinstance(s, _NoopSpan)
        # set_attribute returns None and changes nothing
        assert s.set_attribute("a", 1) is None


def test_noop_span_does_not_suppress_exceptions():
    raised = False
    try:
        with _NoopTracer().span("boom"):
            raise ValueError("kaboom")
    except ValueError:
        raised = True
    assert raised


def test_module_span_shortcut():
    with span("y") as s:
        s.set_attribute("z", 1)


def test_span_does_not_suppress_exceptions():
    raised = False
    try:
        with span("boom"):
            raise ValueError("kaboom")
    except ValueError:
        raised = True
    assert raised
