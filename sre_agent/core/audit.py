"""Structured, secret-scrubbed audit trail.

The audit log is the security-of-record: every classification, approval,
execution, and secret lease is recorded here. All payloads are scrubbed of
secret-looking values before storage (design principle P4 + no-leak rule).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from ..hooks.builtin.secret_scrub import scrub


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    """In-memory audit log with secret scrubbing and JSON export.

    Callable: ``audit("event", {...})`` appends an entry, so it can be passed
    anywhere a ``Callable[[str, dict], None]`` is expected (e.g. ApprovalManager).
    """

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []

    def __call__(self, event: str, data: Dict[str, Any] | None = None) -> None:
        self.record(event, data or {})

    def record(self, event: str, data: Dict[str, Any] | None = None) -> None:
        self._entries.append(
            {
                "event": event,
                "timestamp": _utcnow_iso(),
                "data": scrub(data or {}),  # never store raw secrets
            }
        )

    def entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    def export(self, path: str) -> None:
        """Write the audit log as JSON (0600 — may reference sensitive ops)."""
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, indent=2)


__all__ = ["AuditLog"]
