"""Concrete approval channels."""
from __future__ import annotations

from .gmail import GmailChannel
from .slack import SlackChannel

__all__ = ["SlackChannel", "GmailChannel"]
