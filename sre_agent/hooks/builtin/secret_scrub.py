"""Secret-scrubbing hook.

Recursively redacts values whose *key* looks secret-ish (token, password,
secret, webhook_url, app_password, authorization, api_key, private_key — matched
case-insensitively as a substring) before data is persisted to an audit log or
returned to a caller. Replacement value is ``"<redacted>"``.

Runs at ON_AUDIT and POST_ACTION. Returns a MUTATE outcome carrying a scrubbed
*copy* of ``ctx.data`` so the original in-flight object is not clobbered.
"""
from __future__ import annotations

from typing import Any, List

from ..base import Hook, HookContext, HookOutcome, HookPoint

REDACTED = "<redacted>"

# Substrings that mark a key as secret-bearing (lower-cased comparison).
SECRET_KEY_MARKERS: List[str] = [
    "token",
    "password",
    "secret",
    "webhook_url",
    "app_password",
    "authorization",
    "api_key",
    "private_key",
]


def _is_secret_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    return any(marker in low for marker in SECRET_KEY_MARKERS)


def scrub(obj: Any) -> Any:
    """Return a deep copy of ``obj`` with secret-keyed values redacted.

    Recurses through dicts and lists/tuples. A value is redacted when its dict
    key matches a secret marker, regardless of the value's type.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if _is_secret_key(key):
                out[key] = REDACTED
            else:
                out[key] = scrub(value)
        return out
    if isinstance(obj, list):
        return [scrub(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(scrub(item) for item in obj)
    return obj


class SecretScrub(Hook):
    """ON_AUDIT / POST_ACTION hook that redacts secret-keyed values."""

    name = "secret_scrub"
    points = {HookPoint.ON_AUDIT, HookPoint.POST_ACTION}

    def run(self, ctx: HookContext) -> HookOutcome:
        scrubbed = scrub(ctx.data)
        return HookOutcome.mutate(scrubbed, reason=f"{self.name}: redacted secret-keyed values")


__all__ = ["SecretScrub", "scrub", "REDACTED", "SECRET_KEY_MARKERS"]
