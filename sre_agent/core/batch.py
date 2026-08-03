"""Batch lifecycle: build a batch, then run its APPROVED actions.

Modeled to be workflow-shaped (approval happens out-of-band between build and
run). Already-APPROVED actions are executed with the gate satisfied — the bug
fixed in the prior hardening pass — and never auto-retried.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .executor import Executor
from .models import Action, Batch, OperationResult


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BatchRunner:
    def __init__(self, executor: Executor, audit=None):
        self._executor = executor
        self._audit = audit or (lambda *a, **k: None)

    def build_batch(self, batch_id: str, actions: List[Action],
                    window_minutes: int = 180) -> Batch:
        batch = Batch(batch_id=batch_id, actions=actions,
                      window_minutes=window_minutes, created_at=_utcnow_iso())
        self._audit("batch_created", {"batch_id": batch_id, "actions": len(actions)})
        return batch

    def run_batch(self, batch: Batch, emergency: bool = False) -> List[OperationResult]:
        """Execute every APPROVED action; PENDING/DECLINED are skipped."""
        results: List[OperationResult] = []
        for action in batch.actions:
            if action.status == "APPROVED":
                results.append(self._executor.execute(action, emergency=emergency))
        succeeded = sum(1 for r in results if r.success)
        self._audit("batch_completed", {
            "batch_id": batch.batch_id,
            "executed": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
        })
        return results


__all__ = ["BatchRunner"]
