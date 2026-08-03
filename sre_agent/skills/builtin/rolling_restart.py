"""Rolling restart skill.

Emits one ``restart_vm`` Action per VM, in the order the caller supplied them
(the list order *is* the roll order). The skill plans only; the coordinator's
gate decides how each restart is classified and whether it needs approval.
"""
from __future__ import annotations

from typing import List

from sre_agent.core.models import Action

from ..base import Skill, SkillContext


class RollingRestart(Skill):
    """Restart a set of VMs one at a time."""

    name = "rolling_restart"
    description = "Restart VMs one at a time, in caller-supplied order."

    def plan(self, ctx: SkillContext) -> List[Action]:
        vms = ctx.params.get("vms") or []
        actions: List[Action] = []
        for i, vm in enumerate(vms):
            node = vm.get("node")
            vmid = vm.get("vmid")
            vm_type = vm.get("type", "qemu")
            target = f"{node}:{vmid}"
            actions.append(
                Action(
                    id=f"{self.name}-{i}-{target}",
                    name="restart_vm",
                    target=target,
                    payload={"node": node, "vmid": vmid, "type": vm_type},
                )
            )
        return actions
