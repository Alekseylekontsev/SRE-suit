"""SRE Agent policy-hook layer.

Public surface:

* Contracts: :class:`HookPoint`, :class:`HookDecision`, :class:`HookContext`,
  :class:`HookOutcome`, :class:`Hook`.
* Engine: :class:`HookRegistry`.
* Built-ins: :class:`DestructiveGuard`, :class:`SecretScrub`, :class:`RateLimit`
  (plus the :func:`scrub` helper).
"""
from __future__ import annotations

from .base import Hook, HookContext, HookDecision, HookOutcome, HookPoint
from .builtin import DestructiveGuard, RateLimit, SecretScrub, scrub
from .registry import HookRegistry

__all__ = [
    # contracts
    "HookPoint",
    "HookDecision",
    "HookContext",
    "HookOutcome",
    "Hook",
    # engine
    "HookRegistry",
    # builtins
    "DestructiveGuard",
    "SecretScrub",
    "RateLimit",
    "scrub",
]
