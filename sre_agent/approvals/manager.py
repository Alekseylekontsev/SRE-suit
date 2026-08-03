"""Fail-closed approval manager.

The manager owns the set of :class:`ApprovalChannel` instances and decides
whether a :class:`Batch` may proceed. It is deliberately fail-closed:

* If no channel is enabled, or every enabled channel fails to notify,
  :meth:`ApprovalManager.trigger` returns ``False`` and the caller MUST refuse
  to run the batch.
* Channels that raise are treated as a failed notification, never propagated.

Approval bookkeeping (:meth:`approve`) enforces the batch's approval window:
once ``created_at + window_minutes`` has passed, approvals are rejected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from .base import ApprovalChannel, Batch
from .channels import GmailChannel, SlackChannel

__all__ = ["ApprovalManager"]

# audit callback: (event: str, detail: dict) -> None
AuditFn = Callable[[str, dict], None]
NowFn = Callable[[], datetime]


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalManager:
    """Coordinates approval notifications and approval-window enforcement."""

    def __init__(
        self,
        config: dict,
        channels: Optional[List[ApprovalChannel]] = None,
        audit: Optional[AuditFn] = None,
        now_fn: NowFn = _default_now,
    ) -> None:
        self.config = config or {}
        self.audit = audit
        self.now_fn = now_fn
        approvals_cfg = self.config.get("approvals", {}) or {}
        # Authorized approvers = the allow-list a `approve()` caller must be in.
        # FAIL-CLOSED: if none are configured, no approval can be granted.
        self.authorized_approvers = {
            a.strip().lower() for a in (approvals_cfg.get("authorized_approvers") or []) if a
        }
        # Separation of duties: an approver may not approve their own request
        # unless explicitly allowed in config.
        self.allow_self_approval = bool(approvals_cfg.get("allow_self_approval", False))
        if channels is None:
            channels = self._build_channels(self.config)
        self.channels: List[ApprovalChannel] = list(channels)

    def is_authorized(self, approver: Optional[str]) -> bool:
        return bool(approver) and approver.strip().lower() in self.authorized_approvers

    # ------------------------------------------------------------------ build
    @staticmethod
    def _build_channels(config: dict) -> List[ApprovalChannel]:
        """Build channels from config, honoring ``approvals.primary_channel``."""
        approvals = (config or {}).get("approvals", {})
        primary = approvals.get("primary_channel", "slack")
        channels: List[ApprovalChannel] = []

        if primary in ("slack", "both"):
            slack_cfg = approvals.get("slack", {})
            if slack_cfg.get("enabled") and slack_cfg.get("webhook_url"):
                channels.append(SlackChannel(slack_cfg["webhook_url"]))

        if primary in ("gmail", "both"):
            gmail_cfg = approvals.get("gmail", {})
            if gmail_cfg.get("enabled"):
                channels.append(
                    GmailChannel(
                        group_email=gmail_cfg.get("group_email", ""),
                        from_email=gmail_cfg.get("email", ""),
                        smtp_server=gmail_cfg.get("smtp_server", ""),
                        smtp_port=gmail_cfg.get("smtp_port", 587),
                        app_password=gmail_cfg.get("app_password", ""),
                    )
                )

        return channels

    # ---------------------------------------------------------------- trigger
    def trigger(self, batch: Batch) -> bool:
        """Notify all enabled channels. Fail-closed.

        Returns ``True`` if at least one channel succeeded. Returns ``False``
        if there are no channels or every channel failed (including channels
        that raised). The caller must refuse to run the batch on ``False``.
        """
        if not self.channels:
            self._record("approval_no_channels", {"batch_id": batch.batch_id})
            return False

        any_ok = False
        for channel in self.channels:
            try:
                ok = bool(channel.notify(batch))
            except Exception:
                # Defense in depth: channels should already catch, but a
                # raising channel must never break fail-closed evaluation.
                ok = False
            if ok:
                any_ok = True
            self._record(
                "approval_sent",
                {"batch_id": batch.batch_id, "channel": channel.name, "ok": ok},
            )

        return any_ok

    # ---------------------------------------------------------------- approve
    def is_within_window(self, batch: Batch) -> bool:
        """Whether ``batch`` is still inside its approval window.

        A ``None`` ``created_at`` is treated as just-created (always within
        window). An unparseable timestamp is treated as expired (fail-closed).
        """
        if batch.created_at is None:
            return True
        created = self._parse_iso(batch.created_at)
        if created is None:
            return False
        deadline = created + timedelta(minutes=batch.window_minutes)
        return self._now_aware() <= deadline

    def approve(self, batch: Batch, action_id: str, approver: str) -> bool:
        """Approve matching action(s) in ``batch`` on behalf of ``approver``.

        Fail-closed checks (all must pass, else the action is NOT approved):
        1. ``approver`` is in the configured ``authorized_approvers`` allow-list.
        2. The batch is still within its approval window.
        3. Separation of duties: ``approver`` is not the action's
           ``requested_by`` (unless ``allow_self_approval`` is set).

        ``action_id`` of ``"all"`` applies the per-action checks to every
        action; only the ones that pass are approved. Sets ``status="APPROVED"``
        and records ``action.approver`` only on approved actions. Returns
        ``True`` iff at least one action was approved.
        """
        if not self.is_authorized(approver):
            self._record(
                "approval_denied_unauthorized",
                {"batch_id": batch.batch_id, "action_id": action_id, "approver": approver},
            )
            return False

        if not self.is_within_window(batch):
            self._record(
                "approval_window_expired",
                {"batch_id": batch.batch_id, "action_id": action_id},
            )
            return False

        approved_any = False
        for action in batch.actions:
            if action_id != "all" and action.id != action_id:
                continue
            if not self.allow_self_approval and action.requested_by \
                    and action.requested_by.strip().lower() == approver.strip().lower():
                self._record(
                    "approval_denied_self",
                    {"batch_id": batch.batch_id, "action_id": action.id, "approver": approver},
                )
                continue
            action.status = "APPROVED"
            action.approver = approver
            approved_any = True
            self._record(
                "approval_recorded",
                {"batch_id": batch.batch_id, "action_id": action.id, "approver": approver},
            )
        return approved_any

    # ------------------------------------------------------------------ utils
    def _now_aware(self) -> datetime:
        now = self.now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now

    @staticmethod
    def _parse_iso(value: str) -> Optional[datetime]:
        try:
            dt = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _record(self, event: str, detail: dict) -> None:
        if self.audit is not None:
            try:
                self.audit(event, detail)
            except Exception:
                # Auditing must never affect approval flow.
                pass
