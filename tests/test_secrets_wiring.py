"""The secrets store is a real credential source: connectors can reference a
secret by name (`token_secret`) and the coordinator resolves it via the
SecretsProvider at build time."""
from sre_agent.coordinator import Coordinator, _resolve_secret_refs


class _FakeProvider:
    def __init__(self, values):
        self._v = values

    def get(self, key):
        return self._v.get(key)


def _read_clients(c):
    clients = {}
    for conn in c.registry.connectors():
        if hasattr(conn, "read_clients"):
            clients.update(conn.read_clients())
    return clients


# ---- unit: the resolver ------------------------------------------------------

def test_resolve_secret_refs_fills_token_from_provider():
    cfg = {
        "proxmox": {"nodes": [{"name": "srv1", "host": "https://h:8006",
                               "token_secret": "proxmox_srv1"}]},
        "hetzner": {"token_secret": "hetzner_tok"},
    }
    events = []
    provider = _FakeProvider({"proxmox_srv1": "ptok", "hetzner_tok": "htok"})
    out = _resolve_secret_refs(cfg, provider, lambda e, d: events.append((e, d)))
    assert out["proxmox"]["nodes"][0]["token"] == "ptok"
    assert out["hetzner"]["token"] == "htok"
    # original config not mutated (deep copy)
    assert "token" not in cfg["proxmox"]["nodes"][0]
    assert "secret_resolved" in [e for e, _ in events]


def test_resolve_secret_refs_raw_token_takes_precedence():
    cfg = {"proxmox": {"nodes": [{"name": "s", "host": "https://h:8006",
                                  "token": "inline", "token_secret": "ignored"}]}}
    out = _resolve_secret_refs(cfg, _FakeProvider({"ignored": "X"}), lambda *a: None)
    assert out["proxmox"]["nodes"][0]["token"] == "inline"


def test_resolve_secret_refs_missing_secret_is_audited():
    cfg = {"proxmox": {"nodes": [{"name": "s", "host": "https://h:8006",
                                  "token_secret": "absent"}]}}
    events = []
    _resolve_secret_refs(cfg, _FakeProvider({}), lambda e, d: events.append((e, d)))
    assert "secret_unresolved" in [e for e, _ in events]


# ---- integration: resolved token reaches the connector ----------------------

def test_coordinator_resolves_token_secret_from_env_provider(monkeypatch):
    # EnvFileProvider default prefix SRE_SECRET_ + uppercased key.
    monkeypatch.setenv("SRE_SECRET_PROXMOX_SRV1", "resolved-tok")
    cfg = {
        "proxmox": {"nodes": [{"name": "srv1", "host": "https://h:8006",
                               "token_secret": "proxmox_srv1"}]},
        "hetzner": {"token": "x"},
    }
    c = Coordinator(config=cfg)
    client = _read_clients(c)["srv1"]
    # the resolved secret is what the connector authenticates with
    assert client._auth == ("Authorization", "PVEAPIToken=resolved-tok")
    assert "secret_resolved" in [e["event"] for e in c.audit_log()]


def test_coordinator_unresolved_token_secret_yields_no_client(monkeypatch):
    # no env var set => secret absent => node has no token => connector skips it
    monkeypatch.delenv("SRE_SECRET_PROXMOX_SRV1", raising=False)
    cfg = {"proxmox": {"nodes": [{"name": "srv1", "host": "https://h:8006",
                                  "token_secret": "proxmox_srv1"}]},
           "hetzner": {"token": "x"}}
    c = Coordinator(config=cfg)
    assert _read_clients(c) == {}
    assert "secret_unresolved" in [e["event"] for e in c.audit_log()]
