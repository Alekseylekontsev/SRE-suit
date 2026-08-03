"""Fail-closed approval subsystem for the universal SRE agent."""
from __future__ import annotations

from .base import Action, ApprovalChannel, Batch
from .channels import GmailChannel, SlackChannel
from .manager import ApprovalManager

__all__ = [
    "Action",
    "Batch",
    "ApprovalChannel",
    "ApprovalManager",
    "SlackChannel",
    "GmailChannel",
]
