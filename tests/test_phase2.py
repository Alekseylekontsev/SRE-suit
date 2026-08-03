"""Phase 2 integration: PR_ONLY change-control routing + dataplane/drift wiring."""
from sre_agent.changecontrol.base import ChangeSet, FileChangeControlBackend
from sre_agent.coordinator import Coordinator
from sre_agent.core.classification import Classifier
from sre_agent.core.executor import Executor
from sre_agent.core.models import Action, AutonomyTier, OperationSpec

PR_CFG = {"operations": {"pr_only": ["scale_cluster"]}}


class _FakeRegistry:
    def __init__(self, specs):
        self._specs = {s.name: s for s in specs}

    def spec_for(self, name):
        return self._specs.get(name)


def _spec(name, tier=AutonomyTier.PR_ONLY):
    # handler should NEVER be called for PR_ONLY — make it explode if it is
    def _boom(a):
        raise AssertionError("PR_ONLY handler must not execute directly")
    return OperationSpec(name=name, handler=_boom, tier=tier)


# ---- file backend -----------------------------------------------------------

def test_file_backend_writes_changeset(tmp_path):
    backend = FileChangeControlBackend(str(tmp_path / "changes"))
    cs = ChangeSet(id="cs-1", title="t", items=[])
    cr = backend.propose(cs)
    assert cr.status == "proposed" and cr.url.startswith("file://")
    assert (tmp_path / "changes" / "cs-1.json").exists()


# ---- executor PR_ONLY routing ----------------------------------------------

def test_pr_only_routes_to_change_control(tmp_path):
    backend = FileChangeControlBackend(str(tmp_path / "cc"))
    reg = _FakeRegistry([_spec("scale_cluster")])
    ex = Executor(reg, Classifier(PR_CFG), changecontrol=backend)
    action = Action(id="a1", name="scale_cluster", target="cluster-1", payload={"size": 5})
    r = ex.execute(action)
    assert r.success is True                      # proposing succeeded
    assert action.status == "PROPOSED"            # not DONE — no direct mutation
    assert r.result["change_request"]["status"] == "proposed"


def test_pr_only_without_backend_is_blocked():
    reg = _FakeRegistry([_spec("scale_cluster")])
    r = Executor(reg, Classifier(PR_CFG)).execute(
        Action(id="a1", name="scale_cluster", target="c1"))
    assert r.success is False and "no" in r.error and "change-control" in r.error


def test_pr_only_even_when_approved_still_proposes(tmp_path):
    # PR_ONLY must never mutate directly, even if marked APPROVED
    backend = FileChangeControlBackend(str(tmp_path / "cc"))
    reg = _FakeRegistry([_spec("scale_cluster")])
    ex = Executor(reg, Classifier(PR_CFG), changecontrol=backend)
    a = Action(id="a2", name="scale_cluster", target="c1", status="APPROVED", approver="alice")
    r = ex.execute(a)
    assert r.success and a.status == "PROPOSED"


# ---- coordinator drift + change control ------------------------------------

INVENTORY = {
    "list_nodes": [{"node": "srv1", "nodes": [{"node": "srv1", "status": "online"}]}],
    "list_vms": [{"node": "srv1", "vms": [
        {"vmid": "100", "name": "web", "status": "running", "type": "qemu"}]}],
}


def _coord(tmp_path, extra=None):
    cfg = {
        "proxmox": {"nodes": [{"name": "srv1", "host": "https://h:8006", "token": "t"}]},
        "hetzner": {"token": "x"},
        "changecontrol": {"dir": str(tmp_path / "cc")},
    }
    if extra:
        cfg.update(extra)
    return Coordinator(config=cfg)


def test_coordinator_detect_drift(monkeypatch, tmp_path):
    desired = {"dataplane": {"desired": [
        {"connector": "proxmox", "kind": "vm", "id": "999", "name": "missing-vm"},
    ]}}
    c = _coord(tmp_path, extra=desired)
    monkeypatch.setattr(c, "discover", lambda: INVENTORY)
    findings = c.detect_drift()
    assert any(f.kind.value == "missing" for f in findings)
    assert "drift_detected" in [e["event"] for e in c.audit_log()]


def test_coordinator_remediate_drift_proposes(monkeypatch, tmp_path):
    desired = {"dataplane": {"desired": [
        {"connector": "proxmox", "kind": "vm", "id": "999", "name": "missing-vm"},
    ]}}
    c = _coord(tmp_path, extra=desired)
    monkeypatch.setattr(c, "discover", lambda: INVENTORY)
    requests = c.remediate_drift()
    assert requests and all(r.status == "proposed" for r in requests)
    assert "drift_remediation_proposed" in [e["event"] for e in c.audit_log()]


def test_coordinator_pr_only_op_routes_to_change_control(monkeypatch, tmp_path):
    # mark delete_vm as PR_ONLY via config; executing must propose, not mutate
    c = _coord(tmp_path, extra={"operations": {"pr_only": ["delete_vm"]}})
    r = c.execute("delete_vm", target="srv1:100", payload={"node": "srv1", "vmid": "100"})
    assert r.success and r.result["change_request"]["status"] == "proposed"
    assert "change_proposed" in [e["event"] for e in c.audit_log()]
