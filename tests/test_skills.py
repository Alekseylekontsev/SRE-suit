"""Tests for the skills layer."""
from __future__ import annotations

import copy

import pytest

from sre_agent.skills import (
    BackupRotation,
    RollingRestart,
    Skill,
    SkillContext,
    SkillRegistry,
    default_registry,
)


# --------------------------------------------------------------------------- #
# RollingRestart
# --------------------------------------------------------------------------- #
def test_rolling_restart_emits_one_action_per_vm_in_order():
    vms = [
        {"node": "pve1", "vmid": 100, "type": "qemu"},
        {"node": "pve1", "vmid": 101, "type": "qemu"},
        {"node": "pve2", "vmid": 200, "type": "lxc"},
    ]
    ctx = SkillContext(params={"vms": vms})
    actions = RollingRestart().plan(ctx)

    assert len(actions) == 3
    assert [a.name for a in actions] == ["restart_vm"] * 3
    # Order preserved == roll order.
    assert [a.target for a in actions] == ["pve1:100", "pve1:101", "pve2:200"]
    # Deterministic ids derived from name + index + target.
    assert [a.id for a in actions] == [
        "rolling_restart-0-pve1:100",
        "rolling_restart-1-pve1:101",
        "rolling_restart-2-pve2:200",
    ]
    # Payloads carry node/vmid/type.
    assert actions[2].payload == {"node": "pve2", "vmid": 200, "type": "lxc"}


def test_rolling_restart_default_type_is_qemu():
    ctx = SkillContext(params={"vms": [{"node": "pve1", "vmid": 100}]})
    actions = RollingRestart().plan(ctx)
    assert actions[0].payload["type"] == "qemu"


def test_rolling_restart_empty_or_missing_vms_returns_empty():
    assert RollingRestart().plan(SkillContext(params={"vms": []})) == []
    assert RollingRestart().plan(SkillContext(params={})) == []


# --------------------------------------------------------------------------- #
# BackupRotation
# --------------------------------------------------------------------------- #
def test_backup_rotation_create_snapshot_per_vm():
    vms = [
        {"node": "pve1", "vmid": 100},
        {"node": "pve1", "vmid": 101},
    ]
    ctx = SkillContext(params={"vms": vms, "keep": 3})
    actions = BackupRotation().plan(ctx)

    creates = [a for a in actions if a.name == "create_snapshot"]
    assert len(creates) == 2
    assert creates[0].target == "pve1:100"
    assert creates[0].payload["snapshot"] == "auto-100"
    assert creates[1].payload["snapshot"] == "auto-101"
    # No existing_snapshots -> no deletes.
    assert all(a.name == "create_snapshot" for a in actions)


def test_backup_rotation_deletes_snapshots_beyond_keep():
    vms = [
        {
            "node": "pve1",
            "vmid": 100,
            # oldest-first; keep=2 -> delete the two oldest.
            "existing_snapshots": ["s1", "s2", "s3", "s4"],
        }
    ]
    ctx = SkillContext(params={"vms": vms, "keep": 2})
    actions = BackupRotation().plan(ctx)

    creates = [a for a in actions if a.name == "create_snapshot"]
    deletes = [a for a in actions if a.name == "delete_snapshot"]

    assert len(creates) == 1
    assert len(deletes) == 2
    # Oldest two are pruned.
    assert [d.payload["snapshot"] for d in deletes] == ["s1", "s2"]
    assert all(d.target == "pve1:100" for d in deletes)
    assert [d.id for d in deletes] == [
        "backup_rotation-delete-0-0-pve1:100",
        "backup_rotation-delete-0-1-pve1:100",
    ]


def test_backup_rotation_no_deletes_when_within_keep():
    vms = [{"node": "pve1", "vmid": 100, "existing_snapshots": ["s1", "s2"]}]
    ctx = SkillContext(params={"vms": vms, "keep": 5})
    actions = BackupRotation().plan(ctx)
    assert [a.name for a in actions] == ["create_snapshot"]


# --------------------------------------------------------------------------- #
# SkillRegistry
# --------------------------------------------------------------------------- #
def test_registry_dispatches_by_name():
    reg = SkillRegistry()
    reg.register(RollingRestart())
    ctx = SkillContext(params={"vms": [{"node": "pve1", "vmid": 100}]})

    actions = reg.plan("rolling_restart", ctx)
    assert len(actions) == 1
    assert actions[0].name == "restart_vm"

    assert reg.get("rolling_restart") is not None
    assert reg.get("nope") is None
    assert reg.names() == ["rolling_restart"]


def test_registry_plan_unknown_raises_keyerror():
    reg = SkillRegistry()
    with pytest.raises(KeyError):
        reg.plan("does_not_exist", SkillContext(params={}))


def test_default_registry_has_both_builtins():
    reg = default_registry()
    names = reg.names()
    assert "rolling_restart" in names
    assert "backup_rotation" in names
    assert isinstance(reg.get("rolling_restart"), RollingRestart)
    assert isinstance(reg.get("backup_rotation"), BackupRotation)


# --------------------------------------------------------------------------- #
# Purity / determinism
# --------------------------------------------------------------------------- #
def test_plan_does_not_mutate_ctx_and_is_deterministic():
    vms = [
        {"node": "pve1", "vmid": 100, "existing_snapshots": ["s1", "s2", "s3"]},
        {"node": "pve2", "vmid": 200},
    ]
    params = {"vms": vms, "keep": 1}
    ctx = SkillContext(params=params)
    before = copy.deepcopy(params)

    skill = BackupRotation()
    first = skill.plan(ctx)
    second = skill.plan(ctx)

    # ctx untouched.
    assert ctx.params == before
    # Deterministic: identical ids and shape across repeated calls.
    assert [a.id for a in first] == [a.id for a in second]
    assert [(a.name, a.target, a.payload) for a in first] == [
        (a.name, a.target, a.payload) for a in second
    ]


def test_skill_is_abstract():
    with pytest.raises(TypeError):
        Skill()  # cannot instantiate abstract base
