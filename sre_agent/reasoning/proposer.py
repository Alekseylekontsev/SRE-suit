"""Proposer abstractions for the OPTIONAL LLM reasoning layer.

This layer only *proposes* :class:`~sre_agent.core.models.Action` objects; it
never executes them. The coordinator feeds proposed actions back through the
deterministic gate (classify -> approve -> execute), so the safety path never
depends on anything in this module.

``actions_from_dicts`` is the shared bridge from loosely-typed proposal data
(``{"operation":.., "target":.., "payload":{...}}``) to typed ``Action``
objects. Note the connector contract names the verb field ``Action.name``; the
proposal dicts call it ``operation`` (the SRE-facing term), and we map one to
the other here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from sre_agent.core.models import Action


def actions_from_dicts(items: list[dict]) -> list[Action]:
    """Build :class:`Action` objects from proposal dicts.

    Each item is ``{"operation": str, "target": str, "payload": {...}?}``.
    Ids are deterministic (``proposed-{i}-{operation}``) so the same proposal
    always yields the same ids, which keeps audit/dedup downstream stable.
    """
    actions: list[Action] = []
    for i, item in enumerate(items):
        operation = item.get("operation", "")
        target = item.get("target", "")
        payload = item.get("payload") or {}
        actions.append(
            Action(
                id=f"proposed-{i}-{operation}",
                name=operation,
                target=target,
                payload=dict(payload),
            )
        )
    return actions


@dataclass
class Proposal:
    """A set of proposed actions plus the reasoning behind them."""
    actions: list[Action]
    rationale: str = ""


class Proposer(ABC):
    """Anything that can propose actions toward a goal."""

    @abstractmethod
    def propose(self, goal: str, context: dict | None = None) -> Proposal:
        """Return a :class:`Proposal` of actions for ``goal``."""
        raise NotImplementedError


class StubProposer(Proposer):
    """Offline/deterministic proposer for tests and air-gapped environments.

    Echoes back the actions it was constructed with (or an empty list).
    """

    def __init__(self, actions: list[Action] | None = None) -> None:
        self._actions: list[Action] = list(actions) if actions else []

    def propose(self, goal: str, context: dict | None = None) -> Proposal:
        return Proposal(actions=list(self._actions), rationale="stub")


__all__ = [
    "Proposal",
    "Proposer",
    "StubProposer",
    "actions_from_dicts",
]
