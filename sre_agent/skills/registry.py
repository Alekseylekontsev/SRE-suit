"""Skill registry: name-based lookup and dispatch.

The registry holds :class:`~sre_agent.skills.base.Skill` instances keyed by
their ``name`` and exposes a thin :meth:`SkillRegistry.plan` dispatch helper.
Like the skills themselves, the registry never executes Actions; it only routes
a planning request to the right skill.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sre_agent.core.models import Action

from .base import Skill, SkillContext


class SkillRegistry:
    """A registry of skills addressable by name."""

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register ``skill`` under its ``name`` (overwrites any existing)."""
        if not getattr(skill, "name", ""):
            raise ValueError("skill must define a non-empty 'name'")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        """Return the registered skill for ``name`` or ``None`` if unknown."""
        return self._skills.get(name)

    def names(self) -> List[str]:
        """Return the registered skill names (in registration order)."""
        return list(self._skills.keys())

    def plan(self, name: str, ctx: SkillContext) -> List[Action]:
        """Dispatch to the named skill's :meth:`Skill.plan`.

        Raises ``KeyError`` if ``name`` is not registered.
        """
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(name)
        return skill.plan(ctx)


def default_registry() -> SkillRegistry:
    """Return a registry pre-loaded with the built-in skills."""
    # Imported here to avoid a circular import at module load time.
    from .builtin import BackupRotation, RollingRestart

    registry = SkillRegistry()
    registry.register(RollingRestart())
    registry.register(BackupRotation())
    return registry
