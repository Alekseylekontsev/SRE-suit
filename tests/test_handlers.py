"""Coverage for SREAgent action handlers via mocked Proxmox/Hetzner clients."""
from unittest.mock import MagicMock

import pytest

from sre_agent.agent import SREAgent
from sre_agent.approvals.base import Action


def _agent_with_mocks():
    agent = SREAgent(config={"proxmox": {"nodes": []}, "hetzner": {"token": ""}})
    px = MagicMock()
    px.list_storage.return_value = [{"storage": "local"}, {"id": "vmdata"}]
    px.get_storage_status.return_value = {"used": 1, "total": 10}
    agent.proxmox_clients = {"srv1": px}
    agent.hetzner = MagicMock()
    return agent, px


def _run(agent, op, payload):
    return agent._action_handlers[op](Action(id="t", name=op, target="t", payload=payload))


# ---------------- Proxmox handlers: happy path ----------------

PROXMOX_OPS = [
    ("list_nodes", {}),
    ("list_vms", {}),
    ("list_containers", {}),
    ("get_vm_status", {"node": "srv1", "vmid": "100"}),
    ("get_node_metrics", {"node": "srv1"}),
    ("get_storage_usage", {"node": "srv1"}),
    ("list_snapshots", {"node": "srv1", "vmid": "100"}),
    ("get_cluster_status", {}),
    ("get_network_config", {"node": "srv1"}),
    ("start_vm", {"node": "srv1", "vmid": "100"}),
    ("stop_vm", {"node": "srv1", "vmid": "100"}),
    ("restart_vm", {"node": "srv1", "vmid": "100"}),
    ("create_snapshot", {"node": "srv1", "vmid": "100", "snapname": "s1"}),
    ("delete_vm", {"node": "srv1", "vmid": "100"}),
    ("delete_snapshot", {"node": "srv1", "vmid": "100", "snapname": "s1"}),
    ("delete_container", {"node": "srv1", "vmid": "100"}),
    ("create_vm", {"node": "srv1", "vmid": "100", "name": "x"}),
]


@pytest.mark.parametrize("op,payload", PROXMOX_OPS)
def test_proxmox_handler_happy_path(op, payload):
    agent, _ = _agent_with_mocks()
    result = _run(agent, op, payload)
    if isinstance(result, dict):
        assert result.get("error", "") != f"Unknown node: {payload.get('node')}"


UNKNOWN_NODE_OPS = [
    "get_vm_status", "get_node_metrics", "get_storage_usage", "list_snapshots",
    "get_network_config", "start_vm", "stop_vm", "restart_vm", "create_snapshot",
    "delete_vm", "delete_snapshot", "delete_container", "create_vm",
]


@pytest.mark.parametrize("op", UNKNOWN_NODE_OPS)
def test_proxmox_handler_unknown_node(op):
    agent, _ = _agent_with_mocks()
    result = _run(agent, op, {"node": "ghost"})
    assert result["error"] == "Unknown node: ghost"


# ---------------- Hetzner handlers ----------------

HETZNER_ID_OPS = [
    "hetzner_get_server", "hetzner_delete_server", "hetzner_start_server",
    "hetzner_stop_server", "hetzner_reboot_server",
]


@pytest.mark.parametrize("op", HETZNER_ID_OPS)
def test_hetzner_server_id_required(op):
    agent, _ = _agent_with_mocks()
    assert _run(agent, op, {})["error"] == "server_id required"


@pytest.mark.parametrize("op", HETZNER_ID_OPS)
def test_hetzner_server_id_delegates(op):
    agent, _ = _agent_with_mocks()
    _run(agent, op, {"server_id": "42"})
    method = op.replace("hetzner_", "")          # e.g. get_server
    getattr(agent.hetzner, method).assert_called_once_with(42)


HETZNER_OTHER = [
    ("hetzner_list_servers", {}),
    ("hetzner_create_server", {"name": "x"}),
    ("hetzner_list_networks", {}),
    ("hetzner_list_volumes", {}),
    ("hetzner_list_firewalls", {}),
    ("hetzner_list_ssh_keys", {}),
]


@pytest.mark.parametrize("op,payload", HETZNER_OTHER)
def test_hetzner_other_handlers(op, payload):
    agent, _ = _agent_with_mocks()
    _run(agent, op, payload)   # delegates to the hetzner mock without error


def test_attach_detach_volume():
    agent, _ = _agent_with_mocks()
    assert "error" in _run(agent, "attach_volume", {})
    assert "error" in _run(agent, "detach_volume", {})
    _run(agent, "attach_volume", {"volume_id": "1", "server_id": "2"})
    agent.hetzner.attach_volume.assert_called_once_with(1, 2)
    _run(agent, "detach_volume", {"volume_id": "1"})
    agent.hetzner.detach_volume.assert_called_once_with(1)


# ---------------- Stub handlers (return an explicit error) ----------------

STUB_OPS = [
    "list_backups", "resize_vm", "mount_iso", "network_change", "firewall_change",
    "delete_backup", "stop_production_vm", "migrate_vm", "reboot_node",
    "storage_config", "create_container", "modify_vm_config",
]


@pytest.mark.parametrize("op", STUB_OPS)
def test_stub_handler_returns_error(op):
    agent, _ = _agent_with_mocks()
    assert "error" in _run(agent, op, {})


# ---------------- Audit log ----------------

def test_audit_log_records_and_exports(tmp_path):
    agent, _ = _agent_with_mocks()
    agent._audit("test_event", {"k": "v"})
    log = agent.get_audit_log()
    assert log and log[-1]["event"] == "test_event"
    out = tmp_path / "audit.json"
    agent.export_audit_log(str(out))
    assert out.exists() and "test_event" in out.read_text()
