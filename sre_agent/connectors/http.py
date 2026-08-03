"""Hardened HTTP client shared by all connectors.

Consolidates the duplicated request logic from ``clients/proxmox.py`` (``_request``)
and ``clients/hetzner.py`` (``_req``) into ONE hardened request method. Security
properties (preserved from the two source clients, do not regress):

- **https-only**: a non-``https://`` base URL is rejected in ``__init__``.
- **explicit verified TLS**: a verified ``ssl.SSLContext`` is always built;
  ``ca_cert`` pins a CA bundle (correct way to trust a self-signed Proxmox cert),
  and ``verify_ssl=False`` is a config-gated, operator-acknowledged opt-out.
- **TLS fail-closed**: ``ssl.SSLError`` and ``URLError`` whose ``.reason`` is an
  ``ssl.SSLError`` return ``{"error": "tls_verification_failed", ...}`` and are
  NEVER retried — a failed cert check is a hard security stop.
- **retry policy**: retry only transient HTTP (429/500/502/503/504) and non-TLS
  ``URLError``; never retry 4xx; backoff capped at ``min(2**n, 30)``.
- **URL-encoding**: path segments and query params are percent-encoded
  (improvement over the source clients, which interpolated raw strings).
- **no secret leakage**: auth headers/tokens are never placed in error strings,
  and upstream error bodies are truncated.
"""
from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.request
from typing import Any, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

logger = logging.getLogger(__name__)

# Transient HTTP statuses worth retrying. Everything else (notably 4xx) is a hard
# stop: a 401/403/404 will not become successful and a non-idempotent write must
# not be silently re-issued.
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)

# Cap on the upstream error body we echo back, so a verbose/hostile upstream
# cannot blow up logs or leak large payloads.
_MAX_ERROR_BODY = 2048


def _encode_path(path: str) -> str:
    """Percent-encode each path segment while preserving the ``/`` separators.

    The leading/trailing slashes and segment structure are kept; only the
    individual segments are quoted so e.g. a VM name with spaces or a snapshot
    label is transmitted safely.
    """
    if not path:
        return path
    # Preserve a leading slash, encode each non-empty segment.
    parts = path.split("/")
    encoded = [quote(p, safe="") for p in parts]
    return "/".join(encoded)


class BaseHTTPClient:
    """One hardened ``request`` method for every connector.

    Subclasses provide the auth header via :meth:`_auth_header` (or by passing
    ``auth_header``) and a ``base_url`` that points at the API root (already
    including any common prefix such as ``/api2/json`` or ``/v1``).
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth_header: Optional[tuple[str, str]] = None,
        verify_ssl: bool = True,
        ca_cert: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3,
        default_headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError(
                f"base URL must use https:// (got {self.base_url!r}); refusing to "
                "send credentials over an unencrypted/unvalidated connection"
            )
        self.verify_ssl = verify_ssl
        self.ca_cert = ca_cert
        self.timeout = timeout
        self.max_retries = max_retries
        # Auth header kept private and never embedded in error strings.
        self._auth = auth_header
        self._default_headers = dict(default_headers or {})

    # ------------------------------------------------------------------ TLS
    def _build_ssl_context(self) -> ssl.SSLContext:
        """Build the SSL context (secure by default).

        Order of preference, most to least secure:
        1. ``ca_cert`` set -> verify against that CA bundle (correct way to trust
           a self-signed Proxmox certificate, ``/etc/pve/pve-root-ca.pem``).
        2. ``verify_ssl`` true (default) -> full verification against the system
           trust store.
        3. ``verify_ssl`` false -> verification disabled. An explicit,
           operator-acknowledged opt-out; prefer option 1 instead.
        """
        if self.ca_cert:
            return ssl.create_default_context(cafile=self.ca_cert)
        if self.verify_ssl:
            return ssl.create_default_context()
        logger.warning(
            "TLS verification disabled for %s (verify_ssl=false). Set 'ca_cert' "
            "to the CA bundle to restore verification.",
            self.base_url,
        )
        ctx = ssl.create_default_context()  # NOSONAR - intentional, config-gated opt-out
        ctx.check_hostname = False  # NOSONAR - intentional, config-gated opt-out
        ctx.verify_mode = ssl.CERT_NONE  # NOSONAR - intentional, config-gated opt-out
        return ctx

    # -------------------------------------------------------------- auth hook
    def _auth_header(self) -> Optional[tuple[str, str]]:
        """Return ``(header_name, header_value)`` for auth, or ``None``.

        Subclasses may override; default uses the tuple supplied at construction.
        """
        return self._auth

    # ----------------------------------------------------------------- request
    def request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        _retries: int = 0,
    ) -> Dict[str, Any]:
        """Perform a hardened HTTP request and return a decoded JSON dict.

        On any error a dict with an ``"error"`` key is returned (never raised),
        mirroring the source clients so connectors can branch on it.
        """
        url = self.base_url + _encode_path(path)
        if params:
            # urlencode percent-encodes both keys and values; skip None values.
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urlencode(clean)}"

        req = urllib.request.Request(url, method=method.upper())
        for name, value in self._default_headers.items():
            req.add_header(name, value)
        auth = self._auth_header()
        if auth is not None:
            req.add_header(auth[0], auth[1])
        if data is not None:
            req.data = json.dumps(data).encode("utf-8")
            req.add_header("Content-Type", "application/json")

        ctx = self._build_ssl_context()

        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            # Only retry transient server-side errors; never retry 4xx, and never
            # re-issue a non-idempotent write on a hard error.
            if e.code in _RETRYABLE_STATUS and _retries < self.max_retries:
                time.sleep(min(2 ** _retries, 30))
                return self.request(method, path, data, params, _retries + 1)
            body = e.read(_MAX_ERROR_BODY).decode("utf-8", "replace")
            return {"error": f"HTTP {e.code}", "message": body}
        except ssl.SSLError as e:
            # TLS verification must fail closed: no retry, no downgrade to a
            # generic "unreachable". A failed cert check is a hard security stop.
            logger.error("TLS error talking to %s: %s", self.base_url, e)
            return {"error": "tls_verification_failed", "reason": str(e)}
        except URLError as e:
            # urllib wraps TLS handshake failures in URLError(reason=ssl.SSLError);
            # surface those as fail-closed TLS errors rather than retrying.
            if isinstance(e.reason, ssl.SSLError):
                logger.error("TLS verification failed for %s: %s", self.base_url, e.reason)
                return {"error": "tls_verification_failed", "reason": str(e.reason)}
            if _retries < self.max_retries:
                time.sleep(min(2 ** _retries, 30))
                return self.request(method, path, data, params, _retries + 1)
            return {"error": "unreachable", "reason": str(e.reason)}

    def _request(self, path: str, method: str = "GET",
                 data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convenience GET-style alias used by the monitoring read path
        (which expects a ``client._request(path)`` interface)."""
        return self.request(method, path, data=data)


__all__ = ["BaseHTTPClient"]
