"""Claude-backed proposer.

This is the only place the ``anthropic`` SDK is touched, and it is imported
LAZILY inside :meth:`ClaudeProposer.__init__` so ``import sre_agent.reasoning``
works without anthropic installed. The reasoning layer is OPTIONAL: it proposes
:class:`~sre_agent.core.models.Action` objects and never executes them — every
proposed action is fed back through the deterministic approval gate.

Model + thinking parameters follow the Claude API guidance for ``claude-opus-4-8``:
adaptive thinking (``budget_tokens`` is not supported on this model) and the
``effort`` control under ``output_config``.
"""
from __future__ import annotations

import json
from typing import Any

from sre_agent.reasoning.proposer import Proposal, Proposer, actions_from_dicts

_SYSTEM_PROMPT = (
    "You are an SRE planning assistant. Given a goal and operational context, "
    "propose the infrastructure operations needed to achieve it, expressed as "
    "tool calls via the propose_actions tool. "
    "Do NOT assume these operations will run unguarded: each proposed action "
    "passes through a deterministic safety gate and human approval before it is "
    "ever executed. Propose the minimal, most direct set of operations. Provide "
    "a short rationale explaining why."
)

_TOOLS = [
    {
        "name": "propose_actions",
        "description": (
            "Propose SRE operations to achieve the goal. Each becomes an Action "
            "that passes through approval."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation": {"type": "string"},
                            "target": {"type": "string"},
                            "payload": {
                                "type": "object",
                                "additionalProperties": True,
                            },
                        },
                        "required": ["operation", "target"],
                        "additionalProperties": False,
                    },
                },
                "rationale": {"type": "string"},
            },
            "required": ["actions"],
            "additionalProperties": False,
        },
    }
]


class ClaudeProposer(Proposer):
    """Propose actions by asking Claude to emit a ``propose_actions`` tool call."""

    def __init__(
        self,
        model: str = "claude-opus-4-8",
        client: Any = None,
        max_actions: int = 10,
    ) -> None:
        self.model = model
        self.max_actions = max_actions
        if client is None:
            # Lazy import: keep the safety path independent of anthropic.
            try:
                from anthropic import Anthropic
            except ImportError as exc:  # pragma: no cover - depends on env
                raise RuntimeError(
                    "anthropic SDK not installed; pip install anthropic"
                ) from exc
            self._client = Anthropic()
        else:
            self._client = client

    def propose(self, goal: str, context: dict | None = None) -> Proposal:
        user_content = f"Goal:\n{goal}\n\nContext:\n{json.dumps(context or {})}"
        response = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=_SYSTEM_PROMPT,
            tools=_TOOLS,
            tool_choice={"type": "tool", "name": "propose_actions"},
            messages=[{"role": "user", "content": user_content}],
        )

        block = self._find_tool_use(response)
        if block is None:
            return Proposal(actions=[], rationale="")

        tool_input = getattr(block, "input", None) or {}
        raw_actions = tool_input.get("actions") or []
        actions = actions_from_dicts(raw_actions)[: self.max_actions]
        rationale = tool_input.get("rationale", "")
        return Proposal(actions=actions, rationale=rationale)

    @staticmethod
    def _find_tool_use(response: Any) -> Any:
        """Return the propose_actions tool_use block, or None (defensive)."""
        for block in getattr(response, "content", None) or []:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == "propose_actions"
            ):
                return block
        return None


__all__ = ["ClaudeProposer"]
