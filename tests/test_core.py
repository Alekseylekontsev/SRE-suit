"""Tests for the deterministic control-plane core: classification, executor
gate, batch lifecycle. These pin the safety path (design principle P1/P2)."""

from sre_agent.core.audit import AuditLog
from sre_agent.core.batch import BatchRunner
from sre_agent.core.classification import Classifier, validate
from sre_agent.core.executor import Executor
from sre_agent.core.models import Action, AutonomyTier, OperationSpec
from sre_agent.hooks.base import Hook, HookOutcome, HookPoint
from sre_agent.hooks.registry import HookRegistry

CFG = {
    "operations": {
        "read_only": ["list_vms"],
        "write_safe": ["start_vm"],
        "approval_required": ["delete_vm"],
    },
    "security": {"emergency_auto_actions": ["delete_old_backups"]},
}


class _FakeRegistry:
    def __init__(self, specs):
        self._specs = {s.name: s for s in specs}

    def spec_for(self, name):
        return self._specs.get(name)


def _spec(name, tier, handler=None, secret_keys=None):
    return OperationSpec(name=name, handler=handler or (lambda a: {"ok": True}),
                         tier=tier, secret_keys=secret_keys or [])


# ---- classification ---------------------------------------------------------

def test_classify_tiers():
    c = Classifier(CFG)
    assert c.classify("list_vms") is AutonomyTier.AUTO
    assert c.classify("start_vm") is AutonomyTier.NOTIFY
    assert c.classify("delete_vm") is AutonomyTier.APPROVE


def test_unknown_is_fail_closed_approve():
    assert Classifier(CFG).classify("nope") is AutonomyTier.APPROVE


def test_most_restrictive_wins_on_overlap():
    cfg = {"operations": {"read_only": ["x"], "approval_required": ["x"]}}
    assert Classifier(cfg).classify("x") is AutonomyTier.APPROVE


def test_intrinsic_tier_can_raise_not_lower():
    c = Classifier(CFG)
    # config says AUTO, connector spec says APPROVE → most restrictive APPROVE
    assert c.classify("list_vms", intrinsic=AutonomyTier.APPROVE) is AutonomyTier.APPROVE
    # config says APPROVE, connector says AUTO → still APPROVE
    assert c.classify("delete_vm", intrinsic=AutonomyTier.AUTO) is AutonomyTier.APPROVE


def test_validate_flags_overlap_and_orphan_emergency():
    cfg = {"operations": {"read_only": ["x"], "approval_required": ["x"]},
           "security": {"emergency_auto_actions": ["ghost_op"]}}
    problems = validate(cfg, known_ops={"x"})
    assert any("listed in both" in p for p in problems)
    assert any("ghost_op" in p for p in problems)


# ---- executor gate ----------------------------------------------------------

def test_executor_unknown_op_fails_closed():
    ex = Executor(_FakeRegistry([]), Classifier(CFG))
    r = ex.execute(Action(id="1", name="mystery", target="t"))
    assert r.success is False and "Unknown operation" in r.error


def test_executor_auto_op_runs_and_audits():
    audit = AuditLog()
    reg = _FakeRegistry([_spec("list_vms", AutonomyTier.AUTO, lambda a: [1, 2])])
    ex = Executor(reg, Classifier(CFG), audit=audit)
    r = ex.execute(Action(id="1", name="list_vms", target="t"))
    assert r.success and r.result == [1, 2]
    assert "action_executed" in [e["event"] for e in audit.entries()]


def test_executor_blocks_approval_required():
    reg = _FakeRegistry([_spec("delete_vm", AutonomyTier.APPROVE)])
    r = Executor(reg, Classifier(CFG)).execute(Action(id="1", name="delete_vm", target="t"))
    assert r.success is False and "requires approval" in r.error


def test_executor_runs_when_approved():
    reg = _FakeRegistry([_spec("delete_vm", AutonomyTier.APPROVE, lambda a: {"deleted": True})])
    # APPROVED + an approver stamp (only ApprovalManager sets this) → runs.
    a = Action(id="1", name="delete_vm", target="t", status="APPROVED", approver="alice@example.com")
    r = Executor(reg, Classifier(CFG)).execute(a)
    assert r.success and r.result == {"deleted": True}


def test_executor_approved_status_without_approver_is_blocked():
    # Provenance: status="APPROVED" alone (no approver stamp) must NOT pass —
    # a caller can't self-grant approval by flipping the status field.
    reg = _FakeRegistry([_spec("delete_vm", AutonomyTier.APPROVE, lambda a: {"deleted": True})])
    a = Action(id="1", name="delete_vm", target="t", status="APPROVED")  # no approver
    r = Executor(reg, Classifier(CFG)).execute(a)
    assert r.success is False and "requires approval" in r.error


def test_executor_emergency_downgrades_allowlisted_op():
    cfg = {"operations": {"approval_required": ["delete_old_backups"]},
           "security": {"emergency_auto_actions": ["delete_old_backups"]}}
    reg = _FakeRegistry([_spec("delete_old_backups", AutonomyTier.APPROVE, lambda a: {"ok": 1})])
    r = Executor(reg, Classifier(cfg)).execute(
        Action(id="1", name="delete_old_backups", target="t"), emergency=True)
    assert r.success is True


def test_executor_hook_deny_blocks():
    class _Deny(Hook):
        name = "deny"
        points = {HookPoint.PRE_ACTION}

        def run(self, ctx):
            return HookOutcome.deny("nope")

    hooks = HookRegistry()
    hooks.register(_Deny())
    reg = _FakeRegistry([_spec("list_vms", AutonomyTier.AUTO)])
    r = Executor(reg, Classifier(CFG), hooks=hooks).execute(Action(id="1", name="list_vms", target="t"))
    assert r.success is False and "nope" in r.error


def test_executor_leases_secret_then_runs():
    leased = {}

    class _Secrets:
        def lease(self, key, ttl):
            from sre_agent.core.models import Lease
            leased["key"] = key
            return Lease(key=key, value="s3cr3t", expires_at=9e9)

    reg = _FakeRegistry([_spec("list_vms", AutonomyTier.AUTO, secret_keys=["api"])])
    audit = AuditLog()
    r = Executor(reg, Classifier(CFG), secrets=_Secrets(), audit=audit).execute(
        Action(id="1", name="list_vms", target="t"))
    assert r.success and leased["key"] == "api"
    events = [e["event"] for e in audit.entries()]
    assert "secret_leased" in events
    # the secret value must never appear in the audit trail
    import json
    assert "s3cr3t" not in json.dumps(audit.entries())


def test_executor_missing_secrets_provider_fails_closed():
    reg = _FakeRegistry([_spec("list_vms", AutonomyTier.AUTO, secret_keys=["api"])])
    r = Executor(reg, Classifier(CFG), secrets=None).execute(Action(id="1", name="list_vms", target="t"))
    assert r.success is False


# ---- batch ------------------------------------------------------------------

def test_batch_runs_only_approved():
    reg = _FakeRegistry([_spec("delete_vm", AutonomyTier.APPROVE, lambda a: {"ok": 1})])
    audit = AuditLog()
    runner = BatchRunner(Executor(reg, Classifier(CFG), audit=audit), audit=audit)
    approved = Action(id="a1", name="delete_vm", target="t", status="APPROVED", approver="alice@example.com")
    pending = Action(id="a2", name="delete_vm", target="t")
    declined = Action(id="a3", name="delete_vm", target="t", status="DECLINED")
    batch = runner.build_batch("B", [approved, pending, declined])
    results = runner.run_batch(batch)
    assert len(results) == 1 and results[0].success
    assert pending.status == "PENDING" and declined.status == "DECLINED"
    completed = [e for e in audit.entries() if e["event"] == "batch_completed"][-1]
    assert completed["data"]["executed"] == 1 and completed["data"]["succeeded"] == 1
