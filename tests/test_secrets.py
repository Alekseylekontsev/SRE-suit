"""Tests for the sre_agent.secrets package."""
from __future__ import annotations

import os
import stat

import pytest

from sre_agent.core.errors import SecretError
from sre_agent.secrets import (
    EncryptedSecretStore,
    EnvFileProvider,
    ExternalBackendProvider,
    SecretsProvider,
    load_kek,
)
from sre_agent.secrets import provider as provider_mod

SECRET_VALUE = "hunter2-super-secret-token-9F3a"


def _kek(tmp_path, passphrase="correct horse battery staple"):
    """Build a deterministic passphrase KEK using a persisted salt file."""
    salt_path = str(tmp_path / "kek.salt")
    return load_kek({"source": "passphrase", "passphrase": passphrase, "path": salt_path})


def _store(tmp_path, kek, name="secrets.json"):
    return EncryptedSecretStore(path=str(tmp_path / name), kek=kek)


def test_put_get_round_trip(tmp_path):
    store = _store(tmp_path, _kek(tmp_path))
    store.put("db_password", SECRET_VALUE)
    assert store.get("db_password") == SECRET_VALUE
    assert store.get("missing") is None


def test_wrong_kek_cannot_decrypt(tmp_path):
    path = str(tmp_path / "secrets.json")
    good = _kek(tmp_path, passphrase="right-passphrase")
    EncryptedSecretStore(path=path, kek=good).put("api_key", SECRET_VALUE)

    # A different KEK (different passphrase + salt) must NOT silently return a
    # wrong plaintext; it must fail.
    other_salt = str(tmp_path / "other.salt")
    bad = load_kek({"source": "passphrase", "passphrase": "wrong-passphrase", "path": other_salt})
    bad_store = EncryptedSecretStore(path=path, kek=bad)
    with pytest.raises(SecretError):
        bad_store.get("api_key")


def test_disk_file_has_no_plaintext_and_is_0600(tmp_path):
    path = str(tmp_path / "secrets.json")
    store = EncryptedSecretStore(path=path, kek=_kek(tmp_path))
    store.put("token", SECRET_VALUE)

    raw = open(path, "rb").read()
    assert SECRET_VALUE.encode("utf-8") not in raw
    assert b"token" in raw  # key names are not secret; values are

    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600


def test_delete_removes_secret(tmp_path):
    store = _store(tmp_path, _kek(tmp_path))
    store.put("ephemeral", SECRET_VALUE)
    assert store.get("ephemeral") == SECRET_VALUE
    store.delete("ephemeral")
    assert store.get("ephemeral") is None
    store.delete("ephemeral")  # idempotent


def test_lease_value_matches_and_expires(tmp_path, monkeypatch):
    store = _store(tmp_path, _kek(tmp_path))
    store.put("session", SECRET_VALUE)

    clock = {"t": 1000.0}
    monkeypatch.setattr(provider_mod, "now", lambda: clock["t"])

    lease = store.lease("session", ttl_seconds=30)
    assert lease.value == SECRET_VALUE
    assert lease.key == "session"
    assert not lease.is_expired(clock["t"])
    assert not lease.is_expired(clock["t"] + 29)
    # After the TTL elapses it reports expired.
    assert lease.is_expired(clock["t"] + 30)
    assert lease.is_expired(clock["t"] + 31)


def test_lease_missing_key_raises(tmp_path):
    store = _store(tmp_path, _kek(tmp_path))
    with pytest.raises(SecretError):
        store.lease("nope", ttl_seconds=10)


def test_lease_repr_does_not_leak_value(tmp_path):
    store = _store(tmp_path, _kek(tmp_path))
    store.put("creds", SECRET_VALUE)
    lease = store.lease("creds", ttl_seconds=60)
    text = repr(lease)
    assert SECRET_VALUE not in text
    assert "redacted" in text.lower()


def test_envfile_provider_reads_env_var():
    provider = EnvFileProvider(prefix="SRE_SECRET_", environ={"SRE_SECRET_MYKEY": "envval"})
    assert provider.get("mykey") == "envval"
    assert provider.get("absent") is None


def test_envfile_provider_overlay_and_file(tmp_path):
    path = str(tmp_path / "dev.secrets")
    provider = EnvFileProvider(path=path, environ={})
    provider.put("alpha", "one")
    assert provider.get("alpha") == "one"
    # File written 0600 and round-trips via a fresh provider.
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    reopened = EnvFileProvider(path=path, environ={})
    assert reopened.get("alpha") == "one"
    provider.delete("alpha")
    assert provider.get("alpha") is None


def test_external_backend_is_stub():
    backend = ExternalBackendProvider(backend="vault")
    for call in (lambda: backend.get("k"), lambda: backend.put("k", "v"), lambda: backend.delete("k")):
        with pytest.raises(NotImplementedError):
            call()


def test_load_kek_env_and_file(tmp_path, monkeypatch):
    import base64

    raw = os.urandom(32)
    b64 = base64.b64encode(raw).decode()

    monkeypatch.setenv("MY_KEK", b64)
    assert load_kek({"source": "env", "env_var": "MY_KEK"}) == raw

    key_file = tmp_path / "kek.key"
    key_file.write_text(b64)
    assert load_kek({"source": "file", "path": str(key_file)}) == raw


def test_load_kek_kms_not_implemented():
    with pytest.raises(NotImplementedError):
        load_kek({"source": "kms"})


def test_load_kek_bad_source_raises():
    with pytest.raises(SecretError):
        load_kek({"source": "bogus"})


def test_provider_is_abstract():
    assert issubclass(EncryptedSecretStore, SecretsProvider)
    with pytest.raises(TypeError):
        SecretsProvider()  # cannot instantiate abstract base
