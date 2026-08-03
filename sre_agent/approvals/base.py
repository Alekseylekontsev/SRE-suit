"""Approval channel interface.

``Action`` and ``Batch`` now live canonically in :mod:`sre_agent.core.models`.
They are re-exported here so the legacy import path
``from sre_agent.approvals.base import Action, Batch`` keeps working.

This module also defines :class:`ApprovalChannel`, the abstract base every
notification backend (Slack, Gmail, ...) implements. A channel's
:meth:`ApprovalChannel.notify` MUST return a definite ``True``/``False`` and
must never raise to the caller: the approval subsystem is fail-closed, so a
channel that blows up is treated as a failed notification.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

# Canonical home for the shared data models. Re-exported for backwards compat.
from sre_agent.core.models import Action, Batch

__all__ = ["Action", "Batch", "ApprovalChannel"]


class ApprovalChannel(ABC):
    """A notification backend that asks a human to approve a batch.

    Subclasses are config-bound at construction (e.g. a webhook URL or SMTP
    settings) so the manager can build and reuse them.
    """

    name: str = "channel"

    @abstractmethod
    def notify(self, batch: Batch) -> bool:
        """Notify approvers about ``batch``.

        Returns ``True`` only if the notification was successfully delivered.
        Implementations MUST catch their own exceptions and return ``False``
        rather than propagating; the caller relies on this for fail-closed
        behavior.
        """
        raise NotImplementedError
