"""At-rest encrypted secret store.

:class:`EncryptedSecretStore` persists secrets to a versioned JSON file using
envelope encryption:

* each secret value is encrypted with a fresh random 256-bit DEK using
  AES-256-GCM (random 12-byte nonce per encryption);
* the DEK is then wrapped (encrypted) with the KEK, again via AES-256-GCM with
  its own random nonce.

On-disk format (version 1)::

    {
      "v": 1,
      "secrets": {
        "<key>": {
          "nonce": "<b64 value-nonce>",
          "wrapped_dek": {"nonce": "<b64>", "ct": "<b64 wrapped dek>"},
          "ct": "<b64 ciphertext of the value>"
        }
      }
    }

The file is created and rewritten with mode 0600. Plaintext is never cached on
the instance; values are decrypted on demand inside :meth:`get` and dropped.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sre_agent.core.errors import SecretError

from .provider import SecretsProvider

FORMAT_VERSION = 1
_DEK_LEN = 32     # 256-bit DEK
_NONCE_LEN = 12   # 96-bit GCM nonce (recommended)
_KEK_LEN = 32


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(raw: str) -> bytes:
    return base64.b64decode(raw, validate=True)


class EncryptedSecretStore(SecretsProvider):
    """File-backed, envelope-encrypted secrets provider."""

    def __init__(self, path: str, kek: bytes):
        if not isinstance(kek, (bytes, bytearray)) or len(kek) != _KEK_LEN:
            raise SecretError(f"KEK must be {_KEK_LEN} bytes")
        self._path = path
        self._kek = bytes(kek)
        if not os.path.exists(path):
            self._write_all({})

    # ---- on-disk envelope (de)serialization --------------------------------

    def _read_all(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise SecretError("could not read secret store") from exc
        if not isinstance(doc, dict) or doc.get("v") != FORMAT_VERSION:
            raise SecretError("unsupported or corrupt secret store format")
        secrets = doc.get("secrets")
        if not isinstance(secrets, dict):
            raise SecretError("corrupt secret store: 'secrets' missing")
        return secrets

    def _write_all(self, secrets: Dict[str, Dict[str, Any]]) -> None:
        doc = {"v": FORMAT_VERSION, "secrets": secrets}
        data = json.dumps(doc).encode("utf-8")
        directory = os.path.dirname(os.path.abspath(self._path)) or "."
        # Atomic replace: write a 0600 temp file in the same dir, then rename.
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".secrets-", suffix=".tmp")
        try:
            # ``fchmod`` is POSIX-only.  ``mkstemp`` still creates the file
            # atomically on Windows; the path-level chmod below is portable.
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        # Ensure final file ends up 0600 even if it pre-existed with other bits.
        os.chmod(self._path, 0o600)

    # ---- envelope encryption helpers ---------------------------------------

    def _encrypt_value(self, value: str) -> Dict[str, Any]:
        dek = AESGCM.generate_key(bit_length=256)
        value_nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(dek).encrypt(value_nonce, value.encode("utf-8"), None)

        dek_nonce = os.urandom(_NONCE_LEN)
        wrapped = AESGCM(self._kek).encrypt(dek_nonce, dek, None)
        return {
            "nonce": _b64e(value_nonce),
            "wrapped_dek": {"nonce": _b64e(dek_nonce), "ct": _b64e(wrapped)},
            "ct": _b64e(ct),
        }

    def _decrypt_value(self, entry: Dict[str, Any]) -> str:
        try:
            value_nonce = _b64d(entry["nonce"])
            wrapped = entry["wrapped_dek"]
            dek_nonce = _b64d(wrapped["nonce"])
            wrapped_dek = _b64d(wrapped["ct"])
            ct = _b64d(entry["ct"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SecretError("corrupt secret entry") from exc

        try:
            dek = AESGCM(self._kek).decrypt(dek_nonce, wrapped_dek, None)
            plaintext = AESGCM(dek).decrypt(value_nonce, ct, None)
        except InvalidTag as exc:
            # Wrong KEK or tampered ciphertext — fail loudly, never return junk.
            raise SecretError("failed to decrypt secret (wrong KEK or tampered data)") from exc
        return plaintext.decode("utf-8")

    # ---- SecretsProvider API ------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        secrets = self._read_all()
        entry = secrets.get(key)
        if entry is None:
            return None
        return self._decrypt_value(entry)

    def put(self, key: str, value: str) -> None:
        if not isinstance(value, str):
            raise SecretError("secret value must be a string")
        secrets = self._read_all()
        secrets[key] = self._encrypt_value(value)
        self._write_all(secrets)

    def delete(self, key: str) -> None:
        secrets = self._read_all()
        if key in secrets:
            del secrets[key]
            self._write_all(secrets)


__all__ = ["EncryptedSecretStore", "FORMAT_VERSION"]
