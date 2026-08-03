"""Gmail (SMTP) approval channel.

Wraps the existing :func:`sre_agent.approvals.gmail.notify_batch` logic,
preserving its hardening (``starttls(context=ssl.create_default_context())``
and connection timeout), as an
:class:`~sre_agent.approvals.base.ApprovalChannel`.
"""
from __future__ import annotations

from ..base import ApprovalChannel, Batch
from ..gmail import notify_batch as _gmail_notify_batch

__all__ = ["GmailChannel"]


class GmailChannel(ApprovalChannel):
    """Emails a batch summary to an approvals distribution list over STARTTLS."""

    name = "gmail"

    def __init__(
        self,
        group_email: str,
        from_email: str,
        smtp_server: str,
        smtp_port: int,
        app_password: str,
    ) -> None:
        self.group_email = group_email
        self.from_email = from_email
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.app_password = app_password

    def notify(self, batch: Batch) -> bool:
        try:
            return bool(
                _gmail_notify_batch(
                    batch,
                    self.group_email,
                    self.from_email,
                    self.smtp_server,
                    self.smtp_port,
                    self.app_password,
                )
            )
        except Exception:
            # Fail-closed: never raise to the manager.
            return False
