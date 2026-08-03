"""Tests for the monitoring module."""
from unittest.mock import MagicMock, patch

from sre_agent.monitor import (
    CheckResult,
    check_endpoint,
    check_gitlab,
    check_litellm_health,
    check_litellm_models,
    check_node_workload,
    check_vm_workload,
    format_results,
    run_platform_checks,
)


def test_check_result_defaults():
    r = CheckResult(name="test", target="http://x", status="ok")
    assert r.timestamp != ""
    assert r.response_ms == 0
    assert r.details == {}


def test_check_endpoint_ok():
    with patch("sre_agent.monitor._http_get", return_value=(200, '{"ok":true}', 42.5)):
        r = check_endpoint("test", "http://example.com/health")
    assert r.status == "ok"
    assert r.response_ms == 42.5
    assert r.details["http_status"] == 200


def test_check_endpoint_down():
    with patch("sre_agent.monitor._http_get", return_value=(0, "connection refused", 100)):
        r = check_endpoint("test", "http://dead.host")
    assert r.status == "down"


def test_check_endpoint_degraded():
    with patch("sre_agent.monitor._http_get", return_value=(403, "forbidden", 30)):
        r = check_endpoint("test", "http://example.com")
    assert r.status == "degraded"


def test_check_litellm_health_ok():
    body = '{"status":"healthy","db":"connected","litellm_version":"1.83.9"}'
    with patch("sre_agent.monitor._http_get", return_value=(200, body, 55)):
        r = check_litellm_health({"url": "https://litellm.example.com", "internal_url": ""})
    assert r.status == "ok"
    assert r.details["version"] == "1.83.9"
    assert r.details["db"] == "connected"


def test_check_litellm_health_down():
    with patch("sre_agent.monitor._http_get", return_value=(0, "unreachable", 5000)):
        r = check_litellm_health({"url": "https://dead.host"})
    assert r.status == "down"


def test_check_litellm_models_ok():
    body = '{"data":[{"id":"gpt-4"},{"id":"claude-3"}]}'
    with patch("sre_agent.monitor._http_get", return_value=(200, body, 80)):
        r = check_litellm_models({"url": "https://litellm.example.com", "master_key": "sk-test"})
    assert r.status == "ok"
    assert r.details["model_count"] == 2


def test_check_gitlab_ok():
    body = '{"name_with_namespace":"Admins / AI","default_branch":"main"}'
    with patch("sre_agent.monitor._http_get", return_value=(200, body, 120)):
        r = check_gitlab({"url": "https://gitlab.example.com", "token": "glpat-x", "project": "admins/ai"})
    assert r.status == "ok"
    assert r.details["default_branch"] == "main"


def test_check_gitlab_token_expired():
    with patch("sre_agent.monitor._http_get", return_value=(401, "unauthorized", 50)):
        r = check_gitlab({"url": "https://gitlab.example.com", "token": "dead", "project": "x/y"})
    assert r.status == "degraded"
    assert "expired" in r.details.get("error", "")


def test_check_vm_workload_running():
    mock_client = MagicMock()
    mock_client._request.return_value = {
        "data": {
            "status": "running",
            "name": "LiteLLM",
            "cpu": 0.15,
            "cpus": 8,
            "mem": 4 * 1073741824,
            "maxmem": 16 * 1073741824,
            "disk": 20 * 1073741824,
            "maxdisk": 100 * 1073741824,
            "uptime": 86400,
            "netin": 500 * 1048576,
            "netout": 200 * 1048576,
        }
    }
    r = check_vm_workload(mock_client, "node1", "130")
    assert r.status == "ok"
    assert r.details["vm_status"] == "running"
    assert r.details["cpu_usage"] == 15.0
    assert r.details["mem_pct"] == 25.0
    assert r.details["vm_name"] == "LiteLLM"


def test_check_vm_workload_stopped():
    mock_client = MagicMock()
    mock_client._request.return_value = {
        "data": {"status": "stopped", "name": "test-vm", "cpu": 0, "cpus": 1,
                 "mem": 0, "maxmem": 1073741824, "disk": 0, "maxdisk": 1073741824,
                 "uptime": 0, "netin": 0, "netout": 0}
    }
    r = check_vm_workload(mock_client, "node1", "200")
    assert r.status == "down"


def test_check_vm_workload_high_cpu():
    mock_client = MagicMock()
    mock_client._request.return_value = {
        "data": {"status": "running", "name": "busy", "cpu": 0.95, "cpus": 4,
                 "mem": 1073741824, "maxmem": 8 * 1073741824, "disk": 0,
                 "maxdisk": 1073741824, "uptime": 3600, "netin": 0, "netout": 0}
    }
    r = check_vm_workload(mock_client, "node1", "101")
    assert r.status == "degraded"


def test_check_node_workload():
    mock_client = MagicMock()
    mock_client._request.return_value = {
        "data": {
            "cpu": 0.04,
            "memory": {"used": 400 * 1073741824, "total": 503 * 1073741824},
            "rootfs": {"used": 10 * 1073741824, "total": 100 * 1073741824},
            "uptime": 20000000,
            "loadavg": [3.2, 2.8, 2.5],
        }
    }
    r = check_node_workload(mock_client, "proxmox-srv2")
    assert r.status == "ok"
    assert r.details["cpu_usage"] == 4.0
    assert r.details["uptime_days"] > 200


def test_check_node_workload_handles_non_dict_response():
    # transport failure returning None must not crash the monitor pass
    class _Client:
        def _request(self, path):
            return None

    r = check_node_workload(_Client(), "node1")
    assert r.status == "error"


def test_run_platform_checks_empty():
    results = run_platform_checks({})
    assert results == []


def test_run_platform_checks_with_litellm():
    body = '{"status":"healthy","db":"connected","litellm_version":"1.83.9"}'
    with patch("sre_agent.monitor._http_get", return_value=(200, body, 50)):
        results = run_platform_checks({"litellm": {"url": "https://litellm.test"}})
    names = [r.name for r in results]
    assert "litellm_health" in names
    assert "litellm_models" in names
    assert "litellm_spend" in names


def test_format_results_table():
    results = [
        CheckResult(name="test_ok", target="http://x", status="ok", response_ms=42),
        CheckResult(name="test_down", target="http://y", status="down", response_ms=0),
    ]
    output = format_results(results, "table")
    assert "OK" in output
    assert "DOWN" in output
    assert "Total: 2" in output
    assert "OK: 1" in output
    assert "DOWN: 1" in output


def test_format_results_json():
    results = [CheckResult(name="test", target="http://x", status="ok")]
    output = format_results(results, "json")
    data = __import__("json").loads(output)
    assert len(data) == 1
    assert data[0]["status"] == "ok"
