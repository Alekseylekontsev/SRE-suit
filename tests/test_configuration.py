"""Tests for the typed configuration package (sre_agent.config)."""
import os
import stat

from sre_agent.config import (
    DEFAULT_CONFIG,
    AgentConfig,
    load_config,
    save_config,
    validate,
)
from sre_agent.config.loader import _ensure_defaults


def test_load_defaults():
    cfg = load_config()
    # Defaults are filled in for all top-level sections.
    assert "project" in cfg
    assert "proxmox" in cfg
    assert "approvals" in cfg
    assert "operations" in cfg
    assert "security" in cfg
    assert cfg["approvals"]["batch_window_minutes"] == 180


def test_env_override_coerces_batch_window_to_int(monkeypatch):
    monkeypatch.setenv("SRE_BATCH_WINDOW", "42")
    cfg = load_config()
    value = cfg["approvals"]["batch_window_minutes"]
    assert value == 42
    assert isinstance(value, int)


def test_env_override_bad_int_is_ignored(monkeypatch):
    monkeypatch.setenv("SRE_BATCH_WINDOW", "not-a-number")
    cfg = load_config()
    # Falls back to the default rather than injecting a raw string.
    assert cfg["approvals"]["batch_window_minutes"] == 180


def test_ensure_defaults_does_not_mutate_default_config():
    before = DEFAULT_CONFIG["operations"]["read_only"][:]
    result = _ensure_defaults({})
    # Mutate the returned config's nested list.
    result["operations"]["read_only"].append("__injected__")
    # DEFAULT_CONFIG must be untouched (deepcopy fix).
    assert DEFAULT_CONFIG["operations"]["read_only"] == before
    assert "__injected__" not in DEFAULT_CONFIG["operations"]["read_only"]


def test_agent_config_from_dict_round_trips_key_fields():
    cfg = AgentConfig.from_dict(DEFAULT_CONFIG)
    assert cfg.project.id == DEFAULT_CONFIG["project"]["id"]
    assert cfg.project.name == DEFAULT_CONFIG["project"]["name"]
    assert cfg.approvals.batch_window_minutes == DEFAULT_CONFIG["approvals"]["batch_window_minutes"]
    assert cfg.approvals.slack.channel == DEFAULT_CONFIG["approvals"]["slack"]["channel"]
    assert cfg.approvals.gmail.smtp_port == DEFAULT_CONFIG["approvals"]["gmail"]["smtp_port"]
    assert cfg.operations.read_only == DEFAULT_CONFIG["operations"]["read_only"]
    assert cfg.security.emergency_auto_actions == DEFAULT_CONFIG["security"]["emergency_auto_actions"]


def test_agent_config_from_dict_ignores_unknown_keys():
    cfg = AgentConfig.from_dict({"project": {"id": "x", "bogus": 1}, "totally_unknown": True})
    assert cfg.project.id == "x"
    # Missing keys fall back to defaults.
    assert cfg.proxmox.nodes == []


def test_validate_flags_production_empty_nodes_and_tokens():
    prod_cfg = {
        "project": {"name": "Project_1_production"},
        "proxmox": {"nodes": []},
        "hetzner": {"token": ""},
    }
    problems = validate(prod_cfg)
    assert problems  # non-empty
    joined = " ".join(problems).lower()
    assert "proxmox" in joined
    assert "hetzner" in joined


def test_validate_clean_default_has_no_token_problems():
    # Default config is not production-like, so empty tokens/nodes are fine.
    problems = validate(DEFAULT_CONFIG)
    assert not any("proxmox" in p.lower() for p in problems)
    assert not any("hetzner" in p.lower() for p in problems)
    assert not any("conflicting tiers" in p for p in problems)


def test_validate_flags_conflicting_operation_tiers():
    cfg = {
        "operations": {
            "read_only": ["list_vms", "delete_vm"],
            "approval_required": ["delete_vm"],
        }
    }
    problems = validate(cfg)
    assert any("conflicting tiers" in p for p in problems)


def test_validate_flags_orphan_emergency_action():
    cfg = {
        "operations": {"write_safe": ["stop_vm"]},
        "security": {"emergency_auto_actions": ["nonexistent_op"]},
    }
    problems = validate(cfg)
    assert any("nonexistent_op" in p for p in problems)


def test_save_config_writes_0600(tmp_path):
    path = tmp_path / "out" / "config.json"
    save_config({"project": {"id": "secret"}}, str(path))
    assert path.exists()
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600


def test_load_config_from_json_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"project": {"id": "test", "name": "TestProject"}, "proxmox": {"nodes": []}}')
    cfg = load_config(path=str(path))
    assert cfg["project"]["id"] == "test"
    assert cfg["project"]["name"] == "TestProject"
    assert "approvals" in cfg  # defaults filled in
