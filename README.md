# SRE Suit

SRE Suit is a safety-focused automation agent for Hetzner Cloud and Proxmox VE.
It provides deterministic operation classification, human approval gates,
secret scrubbing, audit logging, monitoring, drift detection, and GitOps change
control.

See the [full agent documentation](sre_agent/README.md) for configuration,
supported operations, security controls, and deployment guidance.

## Development

Requires Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check .
pytest -q
```

On Windows PowerShell, activate the environment with
`.\.venv\Scripts\Activate.ps1`.

Never commit production configuration, credentials, secret-store files, or
private infrastructure addresses. Start from
`sre_agent/config/profiles/config.example.yaml`.
