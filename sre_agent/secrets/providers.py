"""Auxiliary secret providers.

* :class:`EnvFileProvider` — a development-oriented provider that reads secrets
  from environment variables (under a configurable prefix) and/or a flat
  ``key=value`` file. ``put``/``delete`` mutate an in-memory overlay and,
  optionally, persist back to the file. Intended for local/dev use only — it
  offers no at-rest encryption.

* :class:`ExternalBackendProvider` — a documented extension point for external
  secret managers (HashiCorp Vault, AWS/GCP KMS + Secrets Manager, etc.). It is
  a stub: every operation raises ``NotImplementedError``.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

from sre_agent.core.errors import SecretError

from .provider import SecretsProvider

DEFAULT_PREFIX = "SRE_SECRET_"


class EnvFileProvider(SecretsProvider):
    """Read secrets from env vars and/or a flat key=value file (dev only).

    Lookup order for :meth:`get`:

    1. in-memory overlay (anything written via :meth:`put`, minus deletions);
    2. environment variable ``{prefix}{KEY}`` (key upper-cased);
    3. the backing file's parsed contents.

    :meth:`put`/:meth:`delete` update the overlay and, if ``path`` is set,
    rewrite the file so changes survive across processes.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        prefix: str = DEFAULT_PREFIX,
        environ: Optional[Dict[str, str]] = None,
    ):
        self._path = path
        self._prefix = prefix
        # Allow injecting a mapping for tests; default to the live environment.
        self._environ = environ if environ is not None else os.environ
        self._overlay: Dict[str, str] = {}
        self._deleted: set[str] = set()

    # ---- helpers ------------------------------------------------------------

    def _env_name(self, key: str) -> str:
        return f"{self._prefix}{key.upper()}"

    def _read_file(self) -> Dict[str, str]:
        if not self._path or not os.path.exists(self._path):
            return {}
        result: Dict[str, str] = {}
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, _, val = line.partition("=")
                    result[name.strip()] = val.strip()
        except OSError as exc:
            raise SecretError("could not read env secret file") from exc
        return result

    def _write_file(self) -> None:
        if not self._path:
            return
        # Merge file contents with the overlay and removals, then persist 0600.
        merged = self._read_file()
        merged.update(self._overlay)
        for k in self._deleted:
            merged.pop(k, None)
        lines = [f"{k}={v}" for k, v in sorted(merged.items())]
        data = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(self._path, 0o600)

    # ---- SecretsProvider API ------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        if key in self._deleted and key not in self._overlay:
            return None
        if key in self._overlay:
            return self._overlay[key]
        env_val = self._environ.get(self._env_name(key))
        if env_val is not None:
            return env_val
        return self._read_file().get(key)

    def put(self, key: str, value: str) -> None:
        if not isinstance(value, str):
            raise SecretError("secret value must be a string")
        self._overlay[key] = value
        self._deleted.discard(key)
        self._write_file()

    def delete(self, key: str) -> None:
        self._overlay.pop(key, None)
        self._deleted.add(key)
        self._write_file()


class ExternalBackendProvider(SecretsProvider):
    """Stub provider for external secret managers (Vault / cloud KMS).

    This is the documented extension point: subclass it (or fill in the methods)
    to integrate HashiCorp Vault, AWS Secrets Manager + KMS, GCP Secret Manager,
    etc. As shipped, all operations raise ``NotImplementedError``.
    """

    def __init__(self, backend: str = "vault", **options):
        self.backend = backend
        self.options = options

    def get(self, key: str) -> Optional[str]:
        raise NotImplementedError(
            f"ExternalBackendProvider({self.backend!r}) is a stub; "
            "implement get() against your secrets backend"
        )

    def put(self, key: str, value: str) -> None:
        raise NotImplementedError(
            f"ExternalBackendProvider({self.backend!r}) is a stub; "
            "implement put() against your secrets backend"
        )

    def delete(self, key: str) -> None:
        raise NotImplementedError(
            f"ExternalBackendProvider({self.backend!r}) is a stub; "
            "implement delete() against your secrets backend"
        )


__all__ = ["EnvFileProvider", "ExternalBackendProvider", "DEFAULT_PREFIX"]
