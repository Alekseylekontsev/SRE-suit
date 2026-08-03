"""Typed exceptions for the SRE Agent.

Narrow exception types let the executor and coordinator distinguish expected,
operational failures (deny, not-found, secret-missing) from genuine bugs, and
keep secret material out of error strings.
"""
from __future__ import annotations


class SREAgentError(Exception):
    """Base class for all agent errors."""


class ConfigError(SREAgentError):
    """Configuration is missing, malformed, or fails validation."""


class ConnectorError(SREAgentError):
    """A connector could not satisfy a request (transport, auth, bad input)."""


class UnknownOperationError(SREAgentError):
    """No connector exposes the requested operation."""


class ApprovalRequiredError(SREAgentError):
    """An operation needs approval and was not (yet) approved."""


class ApprovalChannelError(SREAgentError):
    """All approval channels failed — the action must fail closed."""


class HookDeniedError(SREAgentError):
    """A policy hook denied the action."""
    def __init__(self, reason: str, hook: str = ""):
        self.reason = reason
        self.hook = hook
        super().__init__(f"denied by hook {hook!r}: {reason}" if hook else reason)


class SecretError(SREAgentError):
    """Secret store/lease failure. Messages MUST NOT contain secret values."""


__all__ = [
    "SREAgentError",
    "ConfigError",
    "ConnectorError",
    "UnknownOperationError",
    "ApprovalRequiredError",
    "ApprovalChannelError",
    "HookDeniedError",
    "SecretError",
]
