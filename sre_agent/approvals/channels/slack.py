"""Slack approval channel.

Wraps the existing :func:`sre_agent.approvals.slack.notify_batch` logic
(Incoming Webhook POST with a 2xx status check) as an
:class:`~sre_agent.approvals.base.ApprovalChannel`.
"""
from __future__ import annotations

from ..base import ApprovalChannel, Batch
from ..slack import notify_batch as _slack_notify_batch

__all__ = ["SlackChannel"]


class SlackChannel(ApprovalChannel):
    """Posts a batch summary to a Slack Incoming Webhook."""

    name = "slack"

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def notify(self, batch: Batch) -> bool:
        if not self.webhook_url:
            return False
        try:
            return bool(_slack_notify_batch(batch, self.webhook_url))
        except Exception:
            # Fail-closed: never raise to the manager.
            return False
