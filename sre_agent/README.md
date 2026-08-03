# Universal SRE Agent (Hetzner + Proxmox)

A flexible, environment-agnostic SRE agent for managing Hetzner Cloud and Proxmox VE infrastructure with human-in-the-loop approvals.

## Documentation

- **[Admin Guide (junior support)](../docs/admin-guide.md)** — how to run the agent safely day-to-day (read-only commands, the approval workflow, what not to do, escalation).
- **[Running in a container](../docs/container.md)** — zero-install usage via Docker / Compose.
- **[Secrets operations](../docs/secrets-ops.md)** — storing/fetching/rotating credentials via Bitwarden so one rotation updates every operator.
- **[Architecture design spec](../docs/superpowers/specs/2026-06-12-sre-agent-architecture-design.md)** — the full platform design.

## Features

- **Multi-environment support**: production, staging, development configurations
- **Human-in-the-loop approvals**: Batch approvals with per-action granularity (Slack/Gmail)
- **Flexible configuration**: JSON, YAML, or environment variables
- **Pluggable architecture**: Easy to extend with new actions and integrations
- **Comprehensive Proxmox support**: VM lifecycle, snapshots, storage, networking
- **Hetzner Cloud integration**: Server provisioning, networks, volumes, load balancers
- **Audit logging**: Complete action trail for compliance

## Quick Start

```bash
# 1. Copy and customize configuration
cp sre_agent/config/profiles/config.example.yaml sre_agent/config.yaml

# 2. Edit configuration with your settings
# At minimum, set:
# - proxmox.nodes[].host and token
# - approvals.slack.webhook_url
# - hetzner.token (if using Hetzner Cloud)

# 3. Run the agent
sre-agent discover
```

### Or run in a container (zero install)

```bash
docker compose build
docker compose run --rm sre-agent health     # needs ./config.yaml mounted
```

See [Running in a container](../docs/container.md) for details.

## Configuration

### Environment Variables

All configuration can be set via environment variables:

```bash
# Project
export SRE_PROJECT_ID="1234"
export SRE_PROJECT_NAME="Project_1"
export SRE_SYSTEM_NAME="DC6"

# Proxmox
export PROXMOX_NODES_0_HOST="https://192.0.2.10:8006"
export PROXMOX_NODES_0_TOKEN="root@pam!sre-agent=<TOKEN>"

# Approvals
export SRE_APPROVAL_CHANNEL="slack"
export SLACK_WEBHOOK="<WEBHOOK_URL>"
```

### YAML Configuration

Create `config.yaml`:

```yaml
project:
  id: "1234"
  name: "Project_1"
  system_name_number: "DC6"

proxmox:
  nodes:
    - name: "srv1"
      host: "https://192.0.2.10:8006"
      token: "root@pam!sre-agent=<TOKEN>"

approvals:
  primary_channel: "slack"
  slack:
    webhook_url: "<WEBHOOK_URL>"
```

## CLI Commands

The console entry point is `sre-agent` (equivalently `python -m sre_agent.cli`).

### Discovery & Monitoring

```bash
sre-agent discover                       # inventory across connectors
sre-agent list-nodes
sre-agent list-vms
sre-agent status --vmid 100 --node srv1
sre-agent monitor                        # health/workload checks
sre-agent graph                          # data-plane resource graph
sre-agent drift                          # desired-vs-actual drift
sre-agent metrics                        # Prometheus-format metrics
```

### Operations & autonomy tiers

Operations are classified into autonomy tiers (`auto` · `notify` · `approve` ·
`pr_only`). Run a single operation directly; risky ones are gated automatically:

```bash
# Auto/notify ops run immediately; approve/pr_only are blocked pending approval
sre-agent run --operation restart_vm --target srv1:100 \
    --payload '{"node":"srv1","vmid":"100"}'
```

### Skills (workflows) & Batch approval

```bash
# A skill plans a workflow into actions submitted through the gate
sre-agent skill --name rolling_restart \
    --params '{"vms":[{"node":"srv1","vmid":"100"}]}'

# Approval batches
sre-agent batch create --operation delete_vm --target srv1:100 \
    --payload '{"node":"srv1","vmid":"100"}'
sre-agent batch list
sre-agent batch approve --action-id action_delete_vm --approver you@example.com
sre-agent batch decline --action-id all
```

### GitOps change control & Emergency mode

```bash
# pr_only ops are proposed as a change request instead of mutated directly
sre-agent remediate                      # propose change-sets for drift

# Emergency bypasses approval ONLY for explicitly allow-listed ops (audited)
sre-agent emergency --operation delete_old_backups --target srv1
```

## Approval Workflow

1. **Create batch**: Agent creates a batch with actions needing approval
2. **Send notification**: Slack message with action details
3. **Wait for approval**: Per-action or batch-level approval
4. **Execute**: Approved actions run automatically
5. **Audit**: All actions logged with timestamps

### Approval Channels

- **Slack**: Primary - webhook notification with action details
- **Gmail/Google Group**: Fallback - email notification

### Batch Options

- **Per-action granularity**: Each action can be approved/declined individually
- **Batch decline**: Allow declining entire batch
- **Window**: Configurable approval window (default: 3 hours)

## Supported Operations

### Read Operations (auto-approved)

- `list_nodes` - List Proxmox nodes
- `list_vms` - List all VMs
- `get_vm_status` - Get VM status
- `get_node_metrics` - Get node metrics
- `get_storage_usage` - Get storage usage
- `list_snapshots` - List snapshots
- `list_backups` - List backups

### Safe Write Operations (auto-approved)

- `start_vm` - Start VM
- `stop_vm` - Stop VM
- `restart_vm` - Reboot VM
- `create_snapshot` - Create snapshot
- `resize_vm` - Resize VM
- `mount_iso` - Mount ISO

### Approval Required Operations

- `delete_vm` - Delete VM
- `delete_snapshot` - Delete snapshot
- `network_change` - Network configuration change
- `firewall_change` - Firewall rule change
- `delete_backup` - Delete backup
- `stop_production_vm` - Stop production VM
- `migrate_vm` - Migrate VM
- `reboot_node` - Reboot node

### Emergency Auto-Actions

These can run without approval in critical situations:

- `delete_old_backups` - Delete old backups
- `stop_non_production_vm` - Stop non-production VMs
- `delete_temp_files` - Delete temporary files

## Project Structure

A deterministic control plane with pluggable layers (full design in
`docs/superpowers/specs/2026-06-12-sre-agent-architecture-design.md`):

```
sre_agent/
├── cli.py                  # CLI entry point (sre-agent)
├── coordinator.py          # orchestrator: intent → classify → gate → dispatch → audit
├── core/                   # models, classification (autonomy tiers), executor, batch, audit, errors
├── config/                 # typed loader + schema + profiles/ (config.<profile>.yaml)
├── connectors/             # Connector ABC + registry + shared hardened HTTP client + proxmox/hetzner
├── secrets/                # encrypted-at-rest store (AES-GCM envelope) + JIT leasing
├── hooks/                  # policy hooks (destructive_guard, secret_scrub, rate_limit)
├── approvals/              # fail-closed approval manager + slack/gmail channels
├── monitoring/             # health/workload checks + runner
├── dataplane/              # resource graph + drift detection
├── changecontrol/          # GitOps PR path (PR_ONLY ops) + GitLab MR backend
├── skills/                 # higher-level workflows (plan → actions through the gate)
├── reasoning/              # optional Claude proposer (lazy; never on the safety path)
├── observability/          # SLO metrics + optional OpenTelemetry tracing
└── naming.py               # resource naming helpers
```

## Environment Profiles

### Production

- Strict approval requirements
- Shorter approval window (60 min)
- Comprehensive audit logging

### Staging

- Moderate approval requirements
- Standard approval window (180 min)
- Full observability

### Development

- Relaxed settings
- Quick approval turnaround
- Minimal logging

## Security

- **Token-based authentication**: API tokens for Proxmox and Hetzner
- **Approval requirements**: All risky operations require human approval
- **Audit logging**: Complete action trail
- **Emergency mode**: Limited auto-actions in critical situations only

## Extending

### Custom Operations

Add custom operations in `agent.py`:

```python
def _handle_custom_operation(self, action: Action) -> Dict:
    # Your implementation
    return {"result": "success"}

# Register handler
self._action_handlers["custom_operation"] = _handle_custom_operation
```

### Custom Approval Logic

Override `requires_approval()` in your agent subclass.

## License

Apache License 2.0. See the repository-level `LICENSE` file.
