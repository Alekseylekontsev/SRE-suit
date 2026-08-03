"""TLS/SSL verification behaviour for the shared HTTP client and monitor checks.

Verifies the secure-by-default policy:
- verification ON by default,
- a CA bundle (ca_cert) is the preferred way to trust self-signed certs,
- verification can only be disabled via an explicit, config-gated opt-out.
"""
import ssl
from unittest.mock import patch

from sre_agent import monitor
from sre_agent.connectors.http import BaseHTTPClient


def test_client_default_context_verifies():
    ctx = BaseHTTPClient("https://h:8006")._build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_client_verify_disabled_is_opt_out():
    ctx = BaseHTTPClient("https://h:8006", verify_ssl=False)._build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_client_ca_cert_uses_cafile():
    with patch("sre_agent.connectors.http.ssl.create_default_context") as m:
        BaseHTTPClient("https://h:8006", ca_cert="/tmp/ca.pem")._build_ssl_context()
    m.assert_called_once_with(cafile="/tmp/ca.pem")


def test_client_ca_cert_takes_precedence_over_verify_false():
    # ca_cert means "verify against this CA" even if verify_ssl was left false.
    with patch("sre_agent.connectors.http.ssl.create_default_context") as m:
        BaseHTTPClient(
            "https://h:8006", verify_ssl=False, ca_cert="/tmp/ca.pem"
        )._build_ssl_context()
    m.assert_called_once_with(cafile="/tmp/ca.pem")


def test_monitor_secure_by_default():
    monitor.configure_tls()  # reset to defaults
    ctx = monitor._ssl_ctx()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_monitor_opt_out():
    try:
        monitor.configure_tls(verify_ssl=False)
        ctx = monitor._ssl_ctx()
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False
    finally:
        monitor.configure_tls()  # reset global state


def test_monitor_ca_cert_uses_cafile():
    try:
        with patch("sre_agent.monitor.ssl.create_default_context") as m:
            monitor.configure_tls(ca_cert="/tmp/ca.pem")
            monitor._ssl_ctx()
        m.assert_called_once_with(cafile="/tmp/ca.pem")
    finally:
        monitor.configure_tls()  # reset global state
