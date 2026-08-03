"""Coordinator wiring + the determinism guarantee (safety path is LLM-free)."""
import sys

from sre_agent.coordinator import Coordinator
from sre_agent.core.models import Action, AutonomyTier

CFG = {
    "proxmox": {"nodes": [{"name": "srv1", "host": "https://h:8006", "token": "t"}]},
    "hetzner": {"token": "x"},
    "approvals": {"authorized_approvers": ["alice@example.com"]},
}


def _coord():
    return Coordinator(config=CFG)


def test_registry_has_expected_ops():
    ops = _coord().registry.operations()
    assert "delete_vm" in ops and "list_vms" in ops and "hetzner_delete_server" in ops


def test_classify_via_coordinator():
    c = _coord()
    assert c.classify("list_vms") is AutonomyTier.AUTO
    assert c.classify("delete_vm") is AutonomyTier.APPROVE


def test_approval_required_blocked_at_gate():
    c = _coord()
    r = c.execute("delete_vm", target="srv1:100", payload={"node": "srv1", "vmid": "100"})
    assert r.success is False and "requires approval" in r.error


def test_force_on_non_allowlisted_op_is_denied_and_audited():
    # force is only honored for emergency_auto_actions; delete_vm isn't one, so
    # force is NOT honored — the op is blocked at the gate and the denial audited.
    c = _coord()
    r = c.execute("delete_vm", target="srv1:100",
                  payload={"node": "srv1", "vmid": "100"}, force=True)
    assert r.success is False and "requires approval" in r.error
    events = [e["event"] for e in c.audit_log()]
    assert "forced_execution_denied" in events
    assert "forced_execution" not in events  # never honored


def test_force_honored_only_for_emergency_allowlisted_op(monkeypatch):
    # An op on the emergency allow-list IS force-executable (and audited).
    from sre_agent.core.models import OperationSpec
    cfg = dict(CFG, security={"emergency_auto_actions": ["delete_vm"]})
    c = Coordinator(config=cfg)
    # stub spec so no network is touched
    fixed = OperationSpec(name="delete_vm", tier=AutonomyTier.APPROVE,
                          handler=lambda a: {"deleted": a.payload.get("vmid")})
    monkeypatch.setattr(c.registry, "spec_for",
                        lambda name: fixed if name == "delete_vm" else None)
    # confirm so the destructive_guard hook also passes
    r = c.execute("delete_vm", target="srv1:100",
                  payload={"node": "srv1", "vmid": "100", "confirm": True}, force=True)
    assert r.success is True
    assert "forced_execution" in [e["event"] for e in c.audit_log()]


def test_batch_approve_executes_via_coordinator(monkeypatch):
    from sre_agent.core.models import OperationSpec
    c = _coord()
    # stub spec resolution so the handler is in-memory (no network touched)
    fixed = OperationSpec(name="delete_vm", tier=AutonomyTier.APPROVE,
                          handler=lambda a: {"deleted": a.payload.get("vmid")})
    monkeypatch.setattr(c.registry, "spec_for",
                        lambda name: fixed if name == "delete_vm" else None)
    action = Action(id="a1", name="delete_vm", target="srv1:100",
                    payload={"node": "srv1", "vmid": "100", "confirm": True})
    c.submit_batch([action])
    results = c.approve("a1", approver="alice@example.com")
    assert results and results[0].success and results[0].result == {"deleted": "100"}
    assert action.approver == "alice@example.com"


def test_safety_path_does_not_import_reasoning_layer():
    """Determinism (P1): exercising the gate must not pull in the LLM layer."""
    sys.modules.pop("sre_agent.reasoning", None)
    c = _coord()
    c.execute("delete_vm", target="t", payload={"node": "srv1", "vmid": "1"})
    assert "sre_agent.reasoning" not in sys.modules
