"""Secrets package for the SRE Agent.

Public API:

* :class:`SecretsProvider` — abstract backend interface (get/put/delete + lease).
* :class:`EncryptedSecretStore` — at-rest encrypted, file-backed store using
  envelope encryption (per-secret DEK wrapped by a KEK, AES-256-GCM).
* :class:`EnvFileProvider` — dev provider over env vars / a flat file.
* :class:`ExternalBackendProvider` — stub extension point for Vault/KMS.
* :func:`load_kek` — resolve a 32-byte key-encryption-key from config.
"""
from __future__ import annotations

from .keys import load_kek
from .provider import SecretsProvider
from .providers import EnvFileProvider, ExternalBackendProvider
from .store import EncryptedSecretStore

__all__ = [
    "SecretsProvider",
    "EncryptedSecretStore",
    "EnvFileProvider",
    "ExternalBackendProvider",
    "load_kek",
]
