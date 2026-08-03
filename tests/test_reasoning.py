"""Tests for the OPTIONAL reasoning layer.

These run with NO network and WITHOUT the anthropic SDK installed. The package
must import cleanly regardless, and ClaudeProposer must be drivable via an
injected fake client (the test seam).
"""
from __future__ import annotations

import importlib

import pytest

from sre_agent.core.models import Action


def test_import_without_anthropic():
    """import sre_agent.reasoning must succeed even when anthropic is absent."""
    mod = importlib.import_module("sre_agent.reasoning")
    assert hasattr(mod, "Proposer")
    assert hasattr(mod, "Proposal")
    assert hasattr(mod, "StubProposer")
    assert hasattr(mod, "ClaudeProposer")
    assert hasattr(mod, "actions_from_dicts")


def test_stub_proposer_echoes_actions():
    from sre_agent.reasoning import Proposal, StubProposer

    given = [Action(id="a1", name="noop", target="srv1", payload={})]
    proposer = StubProposer(actions=given)
    proposal = proposer.propose("do nothing", context={"foo": "bar"})

    assert isinstance(proposal, Proposal)
    assert proposal.actions == given
    assert proposal.rationale == "stub"


def test_stub_proposer_defaults_to_empty():
    from sre_agent.reasoning import StubProposer

    proposal = StubProposer().propose("goal")
    assert proposal.actions == []
    assert proposal.rationale == "stub"


def test_actions_from_dicts_builds_actions_deterministically():
    from sre_agent.reasoning import actions_from_dicts

    items = [
        {"operation": "restart_vm", "target": "srv1:100", "payload": {"vmid": "100"}},
        {"operation": "snapshot", "target": "srv2:200"},
    ]
    actions = actions_from_dicts(items)

    assert len(actions) == 2
    assert actions[0].id == "proposed-0-restart_vm"
    assert actions[0].name == "restart_vm"
    assert actions[0].target == "srv1:100"
    assert actions[0].payload == {"vmid": "100"}

    # Missing payload defaults to an empty dict; ids stay deterministic.
    assert actions[1].id == "proposed-1-snapshot"
    assert actions[1].name == "snapshot"
    assert actions[1].target == "srv2:200"
    assert actions[1].payload == {}


# --- Fakes for the ClaudeProposer test seam -------------------------------

class _FakeBlock:
    def __init__(self, type, name=None, input=None):
        self.type = type
        self.name = name
        self.input = input


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeClient:
    """Duck-typed client exposing .messages.create(**kwargs)."""

    def __init__(self, response, asserts):
        self._response = response
        self._asserts = asserts
        self.messages = self  # so client.messages.create works

    def create(self, **kwargs):
        self._asserts(kwargs)
        return self._response


def test_claude_proposer_parses_tool_use():
    from sre_agent.reasoning import ClaudeProposer, Proposal

    def asserts(kwargs):
        assert kwargs["model"] == "claude-opus-4-8"
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["tool_choice"] == {"type": "tool", "name": "propose_actions"}
        # output_config carries effort (no budget_tokens on this model)
        assert kwargs["output_config"] == {"effort": "high"}
        assert "budget_tokens" not in kwargs.get("thinking", {})

    block = _FakeBlock(
        type="tool_use",
        name="propose_actions",
        input={
            "actions": [
                {
                    "operation": "restart_vm",
                    "target": "srv1:100",
                    "payload": {"node": "srv1", "vmid": "100"},
                }
            ],
            "rationale": "x",
        },
    )
    client = _FakeClient(_FakeResponse(content=[block]), asserts)

    proposer = ClaudeProposer(client=client)
    proposal = proposer.propose("recover srv1", context={"alert": "down"})

    assert isinstance(proposal, Proposal)
    assert len(proposal.actions) == 1
    action = proposal.actions[0]
    assert action.name == "restart_vm"
    assert action.target == "srv1:100"
    assert action.payload == {"node": "srv1", "vmid": "100"}
    assert action.id == "proposed-0-restart_vm"
    assert proposal.rationale == "x"


def test_claude_proposer_defensive_no_tool_use():
    from sre_agent.reasoning import ClaudeProposer, Proposal

    # Only a text block, no tool_use -> empty proposal.
    block = _FakeBlock(type="text")
    client = _FakeClient(_FakeResponse(content=[block]), lambda kwargs: None)

    proposal = ClaudeProposer(client=client).propose("goal")
    assert isinstance(proposal, Proposal)
    assert proposal.actions == []
    assert proposal.rationale == ""


def test_claude_proposer_caps_at_max_actions():
    from sre_agent.reasoning import ClaudeProposer

    items = [
        {"operation": f"op{i}", "target": f"t{i}", "payload": {}}
        for i in range(5)
    ]
    block = _FakeBlock(
        type="tool_use",
        name="propose_actions",
        input={"actions": items, "rationale": "many"},
    )
    client = _FakeClient(_FakeResponse(content=[block]), lambda kwargs: None)

    proposal = ClaudeProposer(client=client, max_actions=2).propose("goal")
    assert len(proposal.actions) == 2
    assert proposal.actions[0].name == "op0"
    assert proposal.actions[1].name == "op1"


def test_claude_proposer_without_client_raises_when_sdk_missing():
    """With client=None and anthropic absent, construction must raise a clear
    RuntimeError. If anthropic somehow IS installed, instantiating is allowed to
    succeed (it would try to read credentials), so we skip that case."""
    from sre_agent.reasoning import ClaudeProposer

    try:
        import anthropic  # noqa: F401
        pytest.skip("anthropic is installed; RuntimeError path not exercised")
    except ImportError:
        pass

    with pytest.raises(RuntimeError, match="anthropic"):
        ClaudeProposer(client=None)
