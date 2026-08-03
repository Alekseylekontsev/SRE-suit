"""Tests for the connectors package.

All HTTP is faked (no network): we either monkeypatch a client's ``request``
method or, for the TLS-fail-closed test, monkeypatch ``urlopen`` to raise.
"""
import ssl
from urllib.error import URLError

import pytest

from sre_agent.connectors import (
    BaseHTTPClient,
    ConnectorRegistry,
    HetznerConnector,
    ProxmoxConnector,
    build_from_config,
)
from sre_agent.core.errors import ConfigError, ConnectorError
from sre_agent.core.models import Action, AutonomyTier


def _action(name, **payload):
    return Action(id="a1", name=name, target="t", payload=payload)


# --------------------------------------------------------------- Proxmox
def test_proxmox_rejects_non_https_host():
    with pytest.raises(ValueError):
        ProxmoxConnector({"pve1": {"host": "http://insecure:8006", "token": "tok"}})


def test_proxmox_operation_tiers():
    conn = ProxmoxConnector({"pve1": {"host": "https://pve1:8006", "token": "tok"}})
    ops = conn.operations()
    assert ops["delete_vm"].tier is AutonomyTier.APPROVE
    assert ops["list_vms"].tier is AutonomyTier.AUTO
    assert ops["start_vm"].tier is AutonomyTier.NOTIFY
    assert ops["migrate_vm"].tier is AutonomyTier.APPROVE
    assert ops["create_snapshot"].tier is AutonomyTier.NOTIFY
    # reads are idempotent
    assert ops["list_vms"].idempotent is True


def test_proxmox_read_returns_parsed_data(monkeypatch):
    conn = ProxmoxConnector({"pve1": {"host": "https://pve1:8006", "token": "tok"}})
    client = conn._clients["pve1"]

    def fake_request(method, path, data=None, params=None, _retries=0):
        assert method == "GET"
        assert path == "/nodes/pve1/qemu/100/status"
        return {"data": {"status": "running", "vmid": 100}}

    monkeypatch.setattr(client, "request", fake_request)
    result = conn.operations()["get_vm_status"].handler(
        _action("get_vm_status", node="pve1", vmid=100)
    )
    assert result == {"status": "running", "vmid": 100}


def test_proxmox_unknown_node_raises():
    conn = ProxmoxConnector({"pve1": {"host": "https://pve1:8006", "token": "tok"}})
    with pytest.raises(ConnectorError):
        conn.operations()["get_vm_status"].handler(
            _action("get_vm_status", node="ghost", vmid=100)
        )


def test_proxmox_non_int_vmid_raises():
    conn = ProxmoxConnector({"pve1": {"host": "https://pve1:8006", "token": "tok"}})
    with pytest.raises(ConnectorError):
        conn.operations()["delete_vm"].handler(
            _action("delete_vm", node="pve1", vmid="not-a-number")
        )


def test_proxmox_unimplemented_stub_raises():
    conn = ProxmoxConnector({"pve1": {"host": "https://pve1:8006", "token": "tok"}})
    with pytest.raises(ConnectorError):
        conn.operations()["network_change"].handler(_action("network_change", node="pve1"))


# --------------------------------------------------------------- Hetzner
def test_hetzner_non_numeric_server_id_raises():
    conn = HetznerConnector("tok")
    with pytest.raises(ConnectorError):
        conn.operations()["hetzner_get_server"].handler(
            _action("hetzner_get_server", server_id="abc")
        )


def test_hetzner_missing_server_id_raises():
    conn = HetznerConnector("tok")
    with pytest.raises(ConnectorError):
        conn.operations()["hetzner_delete_server"].handler(_action("hetzner_delete_server"))


def test_hetzner_tiers():
    ops = HetznerConnector("tok").operations()
    assert ops["hetzner_list_servers"].tier is AutonomyTier.AUTO
    assert ops["hetzner_start_server"].tier is AutonomyTier.NOTIFY
    assert ops["hetzner_create_server"].tier is AutonomyTier.APPROVE
    assert ops["hetzner_delete_server"].tier is AutonomyTier.APPROVE


def test_hetzner_read_returns_data(monkeypatch):
    conn = HetznerConnector("tok")

    def fake_request(method, path, data=None, params=None, _retries=0):
        return {"servers": [{"id": 1, "name": "web"}]}

    monkeypatch.setattr(conn._client, "request", fake_request)
    result = conn.operations()["hetzner_list_servers"].handler(_action("hetzner_list_servers"))
    assert result["servers"][0]["name"] == "web"


# --------------------------------------------------------------- Registry
def test_registry_merges_ops():
    reg = ConnectorRegistry()
    reg.register(ProxmoxConnector({"pve1": {"host": "https://pve1:8006", "token": "t"}}))
    reg.register(HetznerConnector("tok"))
    ops = reg.operations()
    assert "list_vms" in ops
    assert "hetzner_list_servers" in ops
    assert reg.spec_for("delete_vm").tier is AutonomyTier.APPROVE
    assert reg.spec_for("nonexistent") is None


def test_registry_detects_duplicate_op_names():
    reg = ConnectorRegistry()
    reg.register(ProxmoxConnector({"pve1": {"host": "https://pve1:8006", "token": "t"}}))
    # Registering a second Proxmox connector duplicates list_vms etc.
    with pytest.raises(ConfigError):
        reg.register(ProxmoxConnector({"pve2": {"host": "https://pve2:8006", "token": "t"}}))


def test_build_from_config():
    config = {
        "proxmox": {
            "nodes": [
                {"name": "pve1", "host": "https://pve1:8006", "token": "ptok", "verify_ssl": True}
            ]
        },
        "hetzner": {"token": "htok"},
    }
    reg = build_from_config(config)
    names = {c.name for c in reg.connectors()}
    assert names == {"proxmox", "hetzner"}
    ops = reg.operations()
    assert "list_vms" in ops and "hetzner_list_servers" in ops


# --------------------------------------------------------------- TLS fail-closed
def test_base_http_client_rejects_non_https():
    with pytest.raises(ValueError):
        BaseHTTPClient("http://insecure.example")


def test_tls_failure_is_fail_closed_no_retry(monkeypatch):
    """A TLS handshake failure (URLError wrapping ssl.SSLError) must return
    tls_verification_failed and NOT retry."""
    client = BaseHTTPClient("https://api.example/v1", max_retries=3)

    calls = {"n": 0}

    def fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        raise URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    monkeypatch.setattr("sre_agent.connectors.http.urllib.request.urlopen", fake_urlopen)
    result = client.request("GET", "/servers")
    assert result["error"] == "tls_verification_failed"
    assert calls["n"] == 1  # no retry


def test_direct_ssl_error_is_fail_closed(monkeypatch):
    client = BaseHTTPClient("https://api.example/v1", max_retries=3)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        raise ssl.SSLError("handshake failed")

    monkeypatch.setattr("sre_agent.connectors.http.urllib.request.urlopen", fake_urlopen)
    result = client.request("GET", "/x")
    assert result["error"] == "tls_verification_failed"
    assert calls["n"] == 1


def test_path_segments_are_url_encoded(monkeypatch):
    captured = {}
    client = BaseHTTPClient("https://api.example/v1")

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr("sre_agent.connectors.http.urllib.request.urlopen", fake_urlopen)
    client.request("GET", "/servers/a b/snapshot")
    assert "a%20b" in captured["url"]
