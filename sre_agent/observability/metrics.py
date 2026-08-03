"""In-process metrics for the SRE agent: outcome counters and SLO tracking.

Stdlib only. No Prometheus client dependency -- :class:`MetricsRegistry`
exposes a ``render_prometheus()`` helper that emits the Prometheus text
exposition format via plain string building.

The :class:`SLOTracker` is intentionally clock-free: callers pass the
observation timestamp (epoch seconds) so behaviour stays deterministic and
unit-testable.
"""

from __future__ import annotations

from typing import Dict, Tuple

__all__ = ["Counter", "SLOTracker", "MetricsRegistry"]


def _label_key(labels: Dict[str, str]) -> Tuple[Tuple[str, str], ...]:
    """Stable, hashable key for a set of labels (sorted by name)."""
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


class Counter:
    """A labeled, monotonically-increasing counter.

    Values are keyed by their label set, so the same counter can track
    several label combinations (e.g. ``operation="reboot"``).
    """

    def __init__(self, name: str = "", help_text: str = "") -> None:
        self.name = name
        self.help_text = help_text
        self._values: Dict[Tuple[Tuple[str, str], ...], float] = {}

    def inc(self, amount: float = 1, **labels: str) -> None:
        """Increment the counter for the given label set by ``amount``."""
        if amount < 0:
            raise ValueError("counter increment must be non-negative")
        key = _label_key(labels)
        self._values[key] = self._values.get(key, 0) + amount

    def value(self, **labels: str) -> float:
        """Return the current value for the given label set (0 if unseen)."""
        return self._values.get(_label_key(labels), 0)

    def snapshot(self) -> Dict[Tuple[Tuple[str, str], ...], float]:
        """Return a copy of all label-keyed values."""
        return dict(self._values)


class SLOTracker:
    """Tracks availability of monitored targets toward an SLO objective.

    Observations are recorded per target as healthy/unhealthy with a
    caller-supplied timestamp. No internal clock is used.
    """

    def __init__(self, objective: float = 0.9999) -> None:
        self.objective = objective
        # target -> {"observations": int, "healthy": int, "last_ts": float}
        self._targets: Dict[str, Dict[str, float]] = {}

    def record(self, target: str, healthy: bool, ts: float) -> None:
        """Record an observation for ``target`` at epoch second ``ts``."""
        rec = self._targets.setdefault(
            target, {"observations": 0, "healthy": 0, "last_ts": ts}
        )
        rec["observations"] += 1
        if healthy:
            rec["healthy"] += 1
        rec["last_ts"] = ts

    def availability(self, target: str) -> float:
        """Fraction of healthy observations (0..1). 0.0 if no data."""
        rec = self._targets.get(target)
        if not rec or rec["observations"] == 0:
            return 0.0
        return rec["healthy"] / rec["observations"]

    def error_budget_remaining(
        self, target: str, objective: float | None = None
    ) -> float:
        """Remaining error budget, normalized to the objective.

        Defined as ``1 - (1 - availability) / (1 - objective)``:
          * 1.0 when there is zero downtime,
          * ~0.0 when downtime exactly equals the allowed budget,
          * negative when the budget is exhausted.

        Clamped above at 1.0; negative values pass through unclamped.
        """
        obj = self.objective if objective is None else objective
        availability = self.availability(target)
        if obj >= 1.0:
            # Degenerate objective: any downtime exhausts the budget.
            return 1.0 if availability >= 1.0 else float("-inf")
        remaining = 1 - (1 - availability) / (1 - obj)
        return min(remaining, 1.0)

    def summary(self) -> Dict[str, Dict[str, float]]:
        """Per-target {observations, healthy, availability}."""
        out: Dict[str, Dict[str, float]] = {}
        for target, rec in self._targets.items():
            out[target] = {
                "observations": rec["observations"],
                "healthy": rec["healthy"],
                "availability": self.availability(target),
            }
        return out


class MetricsRegistry:
    """Bundles named :class:`Counter` objects with a single SLO tracker."""

    def __init__(self, objective: float = 0.9999) -> None:
        self._counters: Dict[str, Counter] = {}
        self.slo = SLOTracker(objective=objective)

    def counter(self, name: str) -> Counter:
        """Return (creating if needed) the named counter."""
        if name not in self._counters:
            self._counters[name] = Counter(name=name)
        return self._counters[name]

    def record_action(self, operation: str, success: bool) -> None:
        """Record an action outcome.

        Increments ``actions_total`` always and ``actions_failed`` on
        failure, both labeled by ``operation``.
        """
        self.counter("actions_total").inc(operation=operation)
        if not success:
            self.counter("actions_failed").inc(operation=operation)

    def snapshot(self) -> Dict[str, object]:
        """Plain-dict snapshot of all counters plus the SLO summary."""
        return {
            "counters": {
                name: {labels: val for labels, val in counter.snapshot().items()}
                for name, counter in self._counters.items()
            },
            "slo": self.slo.summary(),
        }

    def render_prometheus(self) -> str:
        """Render all counters in Prometheus text exposition format."""
        lines = []
        for name in sorted(self._counters):
            counter = self._counters[name]
            if counter.help_text:
                lines.append(f"# HELP {name} {counter.help_text}")
            lines.append(f"# TYPE {name} counter")
            snap = counter.snapshot()
            if not snap:
                lines.append(f"{name} 0")
                continue
            for labels, val in sorted(snap.items()):
                if labels:
                    rendered = ",".join(
                        f'{k}="{_escape(v)}"' for k, v in labels
                    )
                    lines.append(f"{name}{{{rendered}}} {_fmt(val)}")
                else:
                    lines.append(f"{name} {_fmt(val)}")
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(value: float) -> str:
    # Emit ints without trailing .0 for cleaner exposition output.
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
