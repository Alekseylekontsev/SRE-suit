"""Built-in policy hooks shipped with the SRE Agent."""
from __future__ import annotations

from .destructive_guard import DEFAULT_DESTRUCTIVE_OPS, DestructiveGuard
from .rate_limit import RateLimit
from .secret_scrub import SecretScrub, scrub

__all__ = [
    "DestructiveGuard",
    "DEFAULT_DESTRUCTIVE_OPS",
    "SecretScrub",
    "scrub",
    "RateLimit",
]
