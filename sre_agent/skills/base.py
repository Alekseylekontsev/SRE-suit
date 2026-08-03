"""Skill contracts for the SRE Agent.

A *skill* is a higher-level SRE workflow that decomposes a goal into an ordered
list of :class:`~sre_agent.core.models.Action` objects. It does **not** execute
anything: :meth:`Skill.plan` is a pure function that only constructs Actions.
The coordinator then feeds those Actions back through the deterministic safety
gate (classify -> approve -> execute), so the skill layer never bypasses the
existing safety model.

Invariants for :meth:`Skill.plan`:

* No side effects, no network, no clock/random access.
* Deterministic Action ids (derive them from ``self.name`` + index + target),
  never time-based or random ones, so identical inputs yield identical output.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

from sre_agent.core.models import Action


@dataclass
class SkillContext:
    """Inputs handed to :meth:`Skill.plan`.

    ``params`` carries the skill-specific inputs (e.g. the VM list). ``config``
    is the agent configuration and must be treated as read-only by skills.
    """

    params: dict
    config: dict = field(default_factory=dict)


class Skill(ABC):
    """Base class for all skills.

    Subclasses set the class attributes :attr:`name` and (optionally)
    :attr:`description` and implement the pure :meth:`plan` method.
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    def plan(self, ctx: SkillContext) -> List[Action]:
        """Return the ordered list of Actions that realise this skill.

        Must be pure: no side effects, deterministic output for identical
        ``ctx``. Implementations construct and return Action objects only.
        """
        raise NotImplementedError
