"""SRE Agent skills layer.

Skills decompose a high-level SRE goal into an ordered list of Actions. They
plan only -- the coordinator feeds the returned Actions back through the
deterministic classify -> approve -> execute gate, keeping the safety model
intact.

Public surface:

* Contracts: :class:`Skill`, :class:`SkillContext`.
* Registry: :class:`SkillRegistry`, :func:`default_registry`.
* Built-ins: :class:`RollingRestart`, :class:`BackupRotation`.
"""
from __future__ import annotations

from .base import Skill, SkillContext
from .builtin import BackupRotation, RollingRestart
from .registry import SkillRegistry, default_registry

__all__ = [
    "Skill",
    "SkillContext",
    "SkillRegistry",
    "default_registry",
    "RollingRestart",
    "BackupRotation",
]
