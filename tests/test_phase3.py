"""Phase 3 integration: skills → gate, reasoning proposer → gate, metrics,
and the determinism guarantee (the LLM layer stays off the safety path)."""
import sys

from sre_agent.coordinator import Coordinator
from sre_agent.core.models import Action
from sre_agent.reasoning.proposer import StubProposer

CFG = {
    "proxmox": {"nodes": [{"name": "srv1", "host": "https://h:8006", "token": "t"}]},
    "hetzner": {"token": "x"},
}


def _coord():
    return Coordinator(config=CFG)


# ---- skills → gate ----------------------------------------------------------

def test_run_skill_plans_actions_into_batch():
    c = _coord()
    batch = c.run_skill("rolling_restart",
                        {"vms": [{"node": "srv1", "vmid": "100"},
                                 {"node": "srv1", "vmid": "101"}]})
    assert len(batch.actions) == 2
    assert all(a.name == "restart_vm" for a in batch.actions)
    # not auto-approved → still PENDING (must pass the gate)
    assert all(a.status == "PENDING" for a in batch.actions)
    assert "skill_planned" in [e["event"] for e in c.audit_log()]


def test_run_skill_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        _coord().run_skill("does_not_exist", {})


# ---- reasoning proposer → gate ---------------------------------------------

def test_propose_and_submit_routes_through_gate():
    c = _coord()
    proposed = [Action(id="p1", name="delete_vm", target="srv1:100",
                       payload={"node": "srv1", "vmid": "100"})]
    batch = c.propose_and_submit("clean up old VM", proposer=StubProposer(proposed))
    assert len(batch.actions) == 1
    # proposed actions are NOT auto-approved — they await the gate
    assert batch.actions[0].status == "PENDING"
    assert "actions_proposed" in [e["event"] for e in c.audit_log()]


# ---- metrics ----------------------------------------------------------------

def test_metrics_record_action_outcomes():
    c = _coord()
    # delete_vm is APPROVE → blocked at the gate (no network), success False
    c.execute("delete_vm", target="srv1:100", payload={"node": "srv1", "vmid": "100"})
    assert c.metrics.counter("actions_total").value(operation="delete_vm") >= 1
    assert c.metrics.counter("actions_failed").value(operation="delete_vm") >= 1
    assert "actions_total" in c.metrics.render_prometheus()


# ---- determinism: LLM layer off the safety path -----------------------------

def test_skill_path_does_not_import_reasoning():
    for name in [m for m in sys.modules if m.startswith("sre_agent.reasoning")]:
        del sys.modules[name]
    c = _coord()
    c.run_skill("rolling_restart", {"vms": [{"node": "srv1", "vmid": "100"}]})
    c.execute("delete_vm", target="t", payload={"node": "srv1", "vmid": "1"})
    assert not any(m.startswith("sre_agent.reasoning") for m in sys.modules)
