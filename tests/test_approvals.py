"""Tests for the fail-closed approval subsystem."""
from __future__ import annotations

from datetime import datetime, timezone

from sre_agent.approvals import (
    Action,
    ApprovalChannel,
    ApprovalManager,
    Batch,
    GmailChannel,
    SlackChannel,
)


# --------------------------------------------------------------------- helpers
class _FakeChannel(ApprovalChannel):
    def __init__(self, name: str, result: bool):
        self.name = name
        self._result = result
        self.calls = 0

    def notify(self, batch: Batch) -> bool:
        self.calls += 1
        return self._result


class _RaisingChannel(ApprovalChannel):
    name = "boom"

    def notify(self, batch: Batch) -> bool:
        raise RuntimeError("network down")


def _batch(created_at=None, window_minutes=180):
    return Batch(
        batch_id="B1",
        actions=[
            Action(id="a1", name="stop_vm", target="srv1:100"),
            Action(id="a2", name="start_vm", target="srv1:101"),
        ],
        window_minutes=window_minutes,
        created_at=created_at,
    )


# ----------------------------------------------------------------- trigger
def test_trigger_false_when_all_channels_fail():
    mgr = ApprovalManager({}, channels=[_FakeChannel("a", False), _FakeChannel("b", False)])
    assert mgr.trigger(_batch()) is False


def test_trigger_false_when_no_channel_enabled():
    mgr = ApprovalManager({}, channels=[])
    assert mgr.trigger(_batch()) is False


def test_trigger_false_when_config_disables_all():
    # primary slack but disabled / no webhook -> no channels built -> fail-closed
    cfg = {"approvals": {"primary_channel": "slack", "slack": {"enabled": False}}}
    mgr = ApprovalManager(cfg)
    assert mgr.channels == []
    assert mgr.trigger(_batch()) is False


def test_trigger_true_if_any_channel_succeeds():
    mgr = ApprovalManager({}, channels=[_FakeChannel("a", False), _FakeChannel("b", True)])
    assert mgr.trigger(_batch()) is True


def test_trigger_raising_channel_is_failure_not_propagated():
    raiser = _RaisingChannel()
    mgr = ApprovalManager({}, channels=[raiser])
    # Must not raise, and must be fail-closed.
    assert mgr.trigger(_batch()) is False


def test_trigger_records_per_channel_via_audit():
    events = []
    mgr = ApprovalManager(
        {},
        channels=[_FakeChannel("a", False), _FakeChannel("b", True)],
        audit=lambda e, d: events.append((e, d)),
    )
    mgr.trigger(_batch())
    sent = [d for e, d in events if e == "approval_sent"]
    assert {d["channel"]: d["ok"] for d in sent} == {"a": False, "b": True}


# ----------------------------------------------------------------- approve
# Authorized-approver allow-list config (fail-closed otherwise).
_AUTH = {"approvals": {"authorized_approvers": ["alice@example.com", "bob", "alice"]}}


def _auth_mgr(channels=None, now_fn=None, extra=None):
    cfg = {"approvals": dict(_AUTH["approvals"], **(extra or {}))}
    kwargs = {"channels": channels if channels is not None else []}
    if now_fn is not None:
        kwargs["now_fn"] = now_fn
    return ApprovalManager(cfg, **kwargs)


def test_approve_records_approver_and_flips_status():
    mgr = _auth_mgr(channels=[_FakeChannel("a", True)])
    batch = _batch()
    assert mgr.approve(batch, "a1", "alice@example.com") is True
    a1 = next(a for a in batch.actions if a.id == "a1")
    a2 = next(a for a in batch.actions if a.id == "a2")
    assert a1.status == "APPROVED" and a1.approver == "alice@example.com"
    assert a2.status == "PENDING" and a2.approver is None


def test_approve_all_flips_every_action():
    mgr = _auth_mgr()
    batch = _batch()
    assert mgr.approve(batch, "all", "bob") is True
    assert all(a.status == "APPROVED" and a.approver == "bob" for a in batch.actions)


def test_approve_rejected_when_window_expired():
    created = "2026-01-01T00:00:00+00:00"
    batch = _batch(created_at=created, window_minutes=60)
    past_deadline = datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc)
    mgr = _auth_mgr(now_fn=lambda: past_deadline)
    assert mgr.is_within_window(batch) is False
    assert mgr.approve(batch, "a1", "alice") is False
    assert batch.actions[0].status == "PENDING"


def test_approve_within_window_when_created_at_none():
    batch = _batch(created_at=None, window_minutes=1)
    mgr = _auth_mgr()
    assert mgr.is_within_window(batch) is True
    assert mgr.approve(batch, "a1", "alice") is True


def test_approve_unmatched_action_id_approves_nothing():
    mgr = _auth_mgr()
    batch = _batch()
    assert mgr.approve(batch, "does-not-exist", "alice") is False
    assert all(a.status == "PENDING" for a in batch.actions)


def test_approve_does_not_touch_declined_or_pending_on_mismatch():
    mgr = _auth_mgr()
    batch = _batch()
    batch.actions[0].status = "DECLINED"
    assert mgr.approve(batch, "nope", "alice") is False
    assert batch.actions[0].status == "DECLINED"
    assert batch.actions[1].status == "PENDING"


# ---- authentication & separation of duties (the security fix) --------------
def test_approve_unauthorized_approver_rejected():
    mgr = _auth_mgr()
    batch = _batch()
    assert mgr.approve(batch, "a1", "mallory@evil.com") is False
    assert batch.actions[0].status == "PENDING" and batch.actions[0].approver is None


def test_approve_fails_closed_when_no_approvers_configured():
    # Empty/missing allow-list => nobody can approve.
    mgr = ApprovalManager({}, channels=[])
    batch = _batch()
    assert mgr.approve(batch, "all", "alice@example.com") is False
    assert all(a.status == "PENDING" for a in batch.actions)


def test_approve_blocks_self_approval():
    mgr = _auth_mgr()
    batch = _batch()
    batch.actions[0].requested_by = "alice@example.com"   # alice can't approve her own request
    assert mgr.approve(batch, "a1", "alice@example.com") is False
    assert batch.actions[0].status == "PENDING"


def test_approve_allows_self_approval_when_configured():
    mgr = _auth_mgr(extra={"allow_self_approval": True})
    batch = _batch()
    batch.actions[0].requested_by = "alice@example.com"
    assert mgr.approve(batch, "a1", "alice@example.com") is True
    assert batch.actions[0].status == "APPROVED"


def test_approve_all_skips_self_requested_approves_others():
    mgr = _auth_mgr()
    batch = _batch()
    batch.actions[0].requested_by = "bob"   # bob requested a1; bob approves "all"
    assert mgr.approve(batch, "all", "bob") is True   # at least a2 approved
    assert batch.actions[0].status == "PENDING"       # a1 (self) skipped
    assert batch.actions[1].status == "APPROVED"


# ------------------------------------------------------- backwards-compat
def test_legacy_base_import_still_works():
    from sre_agent.approvals.base import Action as A
    from sre_agent.approvals.base import Batch as B
    from sre_agent.core.models import Action as CA
    from sre_agent.core.models import Batch as CB

    assert A is CA and B is CB


def test_legacy_function_modules_still_import():
    from sre_agent.approvals.gmail import notify_batch as g  # noqa: F401
    from sre_agent.approvals.slack import notify_batch, post_to_slack  # noqa: F401


def test_channels_are_config_bound():
    s = SlackChannel("https://hooks.example/abc")
    assert s.name == "slack" and s.webhook_url == "https://hooks.example/abc"
    g = GmailChannel("grp@x.com", "from@x.com", "smtp.x.com", 587, "pw")
    assert g.name == "gmail" and g.smtp_port == 587
