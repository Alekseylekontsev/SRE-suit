"""Key-encryption-key (KEK) sourcing.

The KEK is the root secret that wraps per-secret data-encryption keys (DEKs) in
:class:`~sre_agent.secrets.store.EncryptedSecretStore`. It is always returned as
a 32-byte (256-bit) key suitable for AES-256-GCM.

Supported sources (``config["source"]``):

* ``"passphrase"`` — derive the key from ``config["passphrase"]`` using scrypt.
  A per-deployment salt is required; supply it as base64 in ``config["salt_b64"]``
  or let the loader generate and persist one to ``config["path"]`` (mode 0600).
* ``"env"`` — read a base64-encoded 32-byte key from the env var named in
  ``config["env_var"]``.
* ``"file"`` — read a base64-encoded 32-byte key from ``config["path"]``.
* ``"kms"`` — placeholder for a cloud KMS hook; raises ``NotImplementedError``.

Never log the passphrase or the derived key. Errors raise
:class:`~sre_agent.core.errors.SecretError` without embedding secret material.
"""
from __future__ import annotations

import base64
import binascii
import os
from typing import Optional

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from sre_agent.core.errors import SecretError

KEK_LEN = 32  # 256-bit key for AES-256-GCM
SALT_LEN = 16

# scrypt work factors. N must be a power of two; these are sane interactive
# defaults (~16 MiB memory) for a control-plane process.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1


def _decode_b64_key(raw: str, *, where: str) -> bytes:
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SecretError(f"KEK from {where} is not valid base64") from exc
    if len(key) != KEK_LEN:
        raise SecretError(
            f"KEK from {where} must be {KEK_LEN} bytes, got {len(key)}"
        )
    return key


def _derive_kek(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(
        salt=salt,
        length=KEK_LEN,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _load_or_create_salt(config: dict) -> bytes:
    """Resolve the scrypt salt for passphrase derivation.

    Precedence: explicit ``salt_b64`` > existing salt file at ``path`` > a freshly
    generated salt persisted to ``path`` (mode 0600). If neither ``salt_b64`` nor
    ``path`` is provided, that is a configuration error (deriving from a random,
    non-persisted salt would make stored secrets undecryptable).
    """
    salt_b64: Optional[str] = config.get("salt_b64")
    if salt_b64:
        try:
            salt = base64.b64decode(salt_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SecretError("KEK salt_b64 is not valid base64") from exc
        if len(salt) < SALT_LEN:
            raise SecretError(f"KEK salt must be >= {SALT_LEN} bytes")
        return salt

    path: Optional[str] = config.get("path")
    if not path:
        raise SecretError(
            "passphrase KEK requires 'salt_b64' or 'path' for the salt file"
        )

    if os.path.exists(path):
        with open(path, "rb") as fh:
            salt = fh.read()
        if len(salt) < SALT_LEN:
            raise SecretError("KEK salt file is too short / corrupt")
        return salt

    # Generate and persist a new salt with restrictive permissions.
    salt = os.urandom(SALT_LEN)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(salt)
    except Exception:
        # Best-effort cleanup so a partial write doesn't poison future runs.
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return salt


def load_kek(config: dict) -> bytes:
    """Return a 32-byte KEK according to ``config``.

    See the module docstring for the config shape. Raises
    :class:`SecretError` on misconfiguration and ``NotImplementedError`` for the
    ``kms`` placeholder source.
    """
    if not isinstance(config, dict):
        raise SecretError("KEK config must be a dict")

    source = config.get("source")
    if not source:
        raise SecretError("KEK config missing 'source'")

    if source == "passphrase":
        passphrase = config.get("passphrase")
        if not passphrase:
            raise SecretError("passphrase KEK source requires 'passphrase'")
        salt = _load_or_create_salt(config)
        return _derive_kek(passphrase, salt)

    if source == "env":
        env_var = config.get("env_var")
        if not env_var:
            raise SecretError("env KEK source requires 'env_var'")
        raw = os.environ.get(env_var)
        if raw is None:
            raise SecretError(f"KEK env var {env_var!r} is not set")
        return _decode_b64_key(raw, where=f"env var {env_var!r}")

    if source == "file":
        path = config.get("path")
        if not path:
            raise SecretError("file KEK source requires 'path'")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
        except OSError as exc:
            raise SecretError("could not read KEK file") from exc
        return _decode_b64_key(raw, where="KEK file")

    if source == "kms":
        raise NotImplementedError(
            "KMS-backed KEK is not implemented; wire a cloud KMS "
            "(e.g. AWS KMS Decrypt / Vault transit) here and return 32 bytes"
        )

    raise SecretError(f"unknown KEK source {source!r}")


__all__ = ["load_kek", "KEK_LEN"]
