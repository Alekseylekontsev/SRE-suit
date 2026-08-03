"""Abstract secrets provider interface.

Every secrets backend (encrypted file store, env-file dev provider, external
Vault/KMS) implements :class:`SecretsProvider`. The base class supplies a
concrete :meth:`lease` built on the abstract get/put/delete primitives so each
backend only has to implement storage.

Secret *values* must never appear in exceptions, logs, or reprs. Failures raise
:class:`~sre_agent.core.errors.SecretError` with a value-free message.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Optional

from sre_agent.core.errors import SecretError
from sre_agent.core.models import Lease


def now() -> float:
    """Wall-clock seam.

    Indirecting through this module-level function lets tests monkeypatch
    ``sre_agent.secrets.provider.now`` (or ``time.time``) to drive lease
    expiry deterministically.
    """
    return time.time()


class SecretsProvider(ABC):
    """Common interface for secret backends."""

    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        """Return the plaintext secret for ``key`` or ``None`` if absent."""
        raise NotImplementedError

    @abstractmethod
    def put(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``, replacing any existing value."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove ``key``. Implementations should be idempotent."""
        raise NotImplementedError

    def lease(self, key: str, ttl_seconds: int) -> Lease:
        """Read ``key`` and wrap it in a short-TTL :class:`Lease`.

        Raises :class:`SecretError` if the key is missing. The returned lease's
        ``value`` is intended for just-in-time injection and should not be
        cached or logged.
        """
        if ttl_seconds <= 0:
            raise SecretError("lease ttl_seconds must be positive")
        value = self.get(key)
        if value is None:
            raise SecretError(f"secret not found for key {key!r}")
        expires_at = now() + ttl_seconds
        return Lease(key=key, value=value, expires_at=expires_at)
