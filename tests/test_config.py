"""Tests for config loading and merging."""
import json
import os
import tempfile

from sre_agent.config import DEFAULT_CONFIG, _ensure_defaults, _merge_config, load_config


def test_defaults_have_all_operation_sections():
    ops = DEFAULT_CONFIG["operations"]
    assert "read_only" in ops
    assert "write_safe" in ops
    assert "approval_required" in ops


def test_defaults_include_hetzner_operations():
    ops = DEFAULT_CONFIG["operations"]
    assert "hetzner_list_servers" in ops["read_only"]
    assert "hetzner_create_server" in ops["approval_required"]


def test_defaults_include_container_operations():
    ops = DEFAULT_CONFIG["operations"]
    assert "list_containers" in ops["read_only"]
    assert "delete_container" in ops["approval_required"]


def test_defaults_include_emergency_auto_actions():
    security = DEFAULT_CONFIG["security"]
    assert "emergency_auto_actions" in security
    assert "delete_old_backups" in security["emergency_auto_actions"]


def test_merge_config_deep():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    override = {"a": {"b": 10, "e": 5}}
    result = _merge_config(base, override)
    assert result == {"a": {"b": 10, "c": 2, "e": 5}, "d": 3}


def test_merge_config_does_not_mutate_base():
    base = {"a": {"b": 1}}
    override = {"a": {"b": 2}}
    _merge_config(base, override)
    assert base["a"]["b"] == 1


def test_ensure_defaults_fills_missing_keys():
    cfg = {"project": {"id": "9999"}}
    result = _ensure_defaults(cfg)
    assert result["project"]["id"] == "9999"
    assert "proxmox" in result
    assert "approvals" in result


def test_load_config_from_json_file():
    cfg_data = {
        "project": {"id": "test", "name": "TestProject"},
        "proxmox": {"nodes": []},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg_data, f)
        f.flush()
        path = f.name

    try:
        result = load_config(path=path)
        assert result["project"]["id"] == "test"
        assert result["project"]["name"] == "TestProject"
        # Defaults should be filled in
        assert "approvals" in result
    finally:
        os.unlink(path)


def test_load_config_env_override(monkeypatch):
    monkeypatch.setenv("SRE_PROJECT_ID", "env-override")
    result = load_config()
    assert result["project"]["id"] == "env-override"
