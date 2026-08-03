"""Backup rotation skill.

For each VM, emits a ``create_snapshot`` Action and, when the caller supplies
the VM's ``existing_snapshots`` (a list of snapshot names, oldest-first), one
``delete_snapshot`` Action per snapshot beyond the ``keep`` retention count.

The skill plans only. The ``delete_snapshot`` Actions are destructive and will
be classified into the APPROVE tier by the downstream gate -- that is the
intended, correct behaviour.
"""
from __future__ import annotations

from typing import List

from sre_agent.core.models import Action

from ..base import Skill, SkillContext


class BackupRotation(Skill):
    """Snapshot each VM and prune snapshots beyond the retention count."""

    name = "backup_rotation"
    description = "Create a snapshot per VM and prune old snapshots beyond 'keep'."

    def plan(self, ctx: SkillContext) -> List[Action]:
        vms = ctx.params.get("vms") or []
        keep = ctx.params.get("keep", 0)
        actions: List[Action] = []

        for i, vm in enumerate(vms):
            node = vm.get("node")
            vmid = vm.get("vmid")
            vm_type = vm.get("type", "qemu")
            target = f"{node}:{vmid}"
            snap_name = f"auto-{vmid}"

            actions.append(
                Action(
                    id=f"{self.name}-create-{i}-{target}",
                    name="create_snapshot",
                    target=target,
                    payload={
                        "node": node,
                        "vmid": vmid,
                        "type": vm_type,
                        "snapshot": snap_name,
                    },
                )
            )

            existing = vm.get("existing_snapshots")
            if not existing:
                continue

            # Snapshots are oldest-first; everything past the newest ``keep``
            # is eligible for deletion.
            if keep > 0:
                to_delete = existing[:-keep]
            else:
                to_delete = list(existing)

            for j, old_snap in enumerate(to_delete):
                actions.append(
                    Action(
                        id=f"{self.name}-delete-{i}-{j}-{target}",
                        name="delete_snapshot",
                        target=target,
                        payload={
                            "node": node,
                            "vmid": vmid,
                            "type": vm_type,
                            "snapshot": old_snap,
                        },
                    )
                )

        return actions
