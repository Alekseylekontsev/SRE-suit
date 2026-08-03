"""Tests for the dataplane package: resource graph + drift detection."""
from __future__ import annotations

from sre_agent.changecontrol.base import ChangeSet
from sre_agent.dataplane import (
    DesiredResource,
    DesiredState,
    DriftKind,
    build_from_coordinator,
    build_from_inventory,
    detect_drift,
    drift_to_changesets,
)


def _sample_inventory() -> dict:
    return {
        "list_nodes": [
            {"node": "pve1", "nodes": [
                {"node": "pve1", "status": "online"},
                # malformed: not a dict — must be skipped, not raise
                "garbage",
                {"status": "online"},  # no id — skipped
            ]},
            {"node": "pve2", "error": "unreachable"},  # error entry — skipped
        ],
        "list_vms": [
            {"node": "pve1", "vms": [
                {"vmid": 100, "name": "web", "status": "running", "type": "qemu"},
                {"vmid": 200, "name": "db", "status": "stopped", "type": "lxc"},
                None,  # malformed — skipped
            ]},
        ],
        "hetzner_list_servers": {"servers": [
            {"id": 42, "name": "edge", "status": "running"},
            {"name": "no-id"},  # no id — skipped
        ]},
    }


def test_build_from_inventory_normalizes_kinds_and_keys():
    graph = build_from_inventory(_sample_inventory())

    node = graph.get("proxmox:node:pve1")
    assert node is not None
    assert node.kind == "node" and node.status == "online"

    vm = graph.get("proxmox:vm:100")
    assert vm is not None
    assert vm.kind == "vm" and vm.name == "web" and vm.status == "running"

    ct = graph.get("proxmox:container:200")
    assert ct is not None and ct.kind == "container"

    server = graph.get("hetzner:server:42")
    assert server is not None
    assert server.kind == "server" and server.connector == "hetzner"

    assert {r.key for r in graph.by_connector("proxmox")} == {
        "proxmox:node:pve1", "proxmox:vm:100", "proxmox:container:200",
    }
    assert len(graph) == 4
    assert graph.keys() == {
        "proxmox:node:pve1", "proxmox:vm:100",
        "proxmox:container:200", "hetzner:server:42",
    }


def test_build_from_inventory_tolerates_malformed_without_raising():
    # Entirely malformed shapes must not raise.
    assert len(build_from_inventory({"list_nodes": "nope", "list_vms": 123})) == 0
    assert len(build_from_inventory({})) == 0
    assert len(build_from_inventory({"hetzner_list_servers": {"servers": "bad"}})) == 0
    # The sample's bad entries are skipped but good ones survive.
    assert len(build_from_inventory(_sample_inventory())) == 4


def test_detect_drift_missing_status_and_unexpected():
    graph = build_from_inventory(_sample_inventory())
    desired = DesiredState(resources=[
        # present, but status mismatch (running vs stopped)
        DesiredResource(kind="vm", id="100", connector="proxmox",
                        expected_status="stopped"),
        # desired but absent from the graph
        DesiredResource(kind="server", id="999", connector="hetzner"),
        # present and matching
        DesiredResource(kind="node", id="pve1", connector="proxmox",
                        expected_status="online"),
    ])

    open_findings = detect_drift(desired, graph, closed_world=False)
    kinds = {f.kind for f in open_findings}
    assert DriftKind.MISSING in kinds
    assert DriftKind.STATUS_DRIFT in kinds
    assert DriftKind.UNEXPECTED not in kinds

    missing = [f for f in open_findings if f.kind == DriftKind.MISSING]
    assert missing[0].resource_key == "hetzner:server:999"

    closed_findings = detect_drift(desired, graph, closed_world=True)
    unexpected = {f.resource_key for f in closed_findings
                  if f.kind == DriftKind.UNEXPECTED}
    # container:200 and server:42 are live but not desired
    assert "proxmox:container:200" in unexpected
    assert "hetzner:server:42" in unexpected


def test_detect_drift_attribute_drift():
    graph = build_from_inventory(_sample_inventory())
    desired = DesiredState(resources=[
        DesiredResource(kind="vm", id="100", connector="proxmox",
                        expected_attributes={"name": "renamed"}),
    ])
    findings = detect_drift(desired, graph)
    assert any(f.kind == DriftKind.ATTRIBUTE_DRIFT for f in findings)


def test_drift_to_changesets_origin_and_deterministic_ids():
    graph = build_from_inventory(_sample_inventory())
    desired = DesiredState(resources=[
        DesiredResource(kind="server", id="999", connector="hetzner"),
        DesiredResource(kind="vm", id="100", connector="proxmox",
                        expected_status="stopped"),
    ])
    findings = detect_drift(desired, graph)
    changesets = drift_to_changesets(findings)

    assert changesets, "expected at least one changeset"
    assert all(isinstance(cs, ChangeSet) for cs in changesets)
    assert all(cs.origin == "drift" for cs in changesets)
    assert all(cs.items for cs in changesets)

    # ids are deterministic: same findings -> same ids
    again = drift_to_changesets(detect_drift(desired, graph))
    assert [cs.id for cs in changesets] == [cs.id for cs in again]
    # ids are unique and carry index + key hash
    ids = [cs.id for cs in changesets]
    assert len(set(ids)) == len(ids)
    assert ids[0].startswith("drift-0-")

    # MISSING maps to a create op
    missing_cs = [cs for cs in changesets
                  if cs.items[0].params["drift_kind"] == DriftKind.MISSING.value]
    assert missing_cs and missing_cs[0].items[0].operation == "create_resource"


def test_desired_state_from_config():
    config = {"dataplane": {"desired": [
        {"kind": "vm", "id": "100", "connector": "proxmox",
         "expected_status": "running"},
        {"bad": "entry"},  # no kind/id -> skipped
    ]}}
    state = DesiredState.from_config(config)
    assert len(state.resources) == 1
    assert state.resources[0].key == "proxmox:vm:100"
    # absent section -> empty, no raise
    assert DesiredState.from_config({}).resources == []
    assert DesiredState.from_config({"dataplane": {}}).resources == []


def test_build_from_coordinator_with_fake():
    class FakeCoordinator:
        def discover(self):
            return {
                "list_nodes": [{"node": "n1", "nodes": [
                    {"node": "n1", "status": "online"}]}],
                "hetzner_list_servers": {"servers": [
                    {"id": 7, "name": "srv", "status": "running"}]},
            }

    graph = build_from_coordinator(FakeCoordinator())
    assert graph.get("proxmox:node:n1") is not None
    assert graph.get("hetzner:server:7") is not None
    assert len(graph) == 2
