"""OPTIONAL LLM reasoning layer for the SRE agent.

This package only *proposes* actions; it never executes them. Importing it must
not require the ``anthropic`` SDK — the SDK is lazy-imported inside
:class:`ClaudeProposer` so the deterministic safety path stays independent of it.
"""
from __future__ import annotations

from sre_agent.reasoning.claude import ClaudeProposer
from sre_agent.reasoning.proposer import (
    Proposal,
    Proposer,
    StubProposer,
    actions_from_dicts,
)

__all__ = [
    "Proposer",
    "Proposal",
    "StubProposer",
    "ClaudeProposer",
    "actions_from_dicts",
]
