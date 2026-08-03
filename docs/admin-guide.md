# SRE Agent — Admin Guide (Junior Support)

A practical, copy‑paste guide for running the SRE Agent day‑to‑day. You do **not**
need to understand the code. Follow the steps, stay inside the "safe" commands,
and escalate anything that asks for approval or looks wrong.

> **The one rule that keeps you safe:** the agent classifies every operation into
> a tier — **auto**, **notify**, **approve**, **pr_only**. Read‑only and routine
> commands run immediately. Anything risky is **blocked until a human approves
> it**. You cannot accidentally delete a VM by running a command — the agent
> stops and waits. When in doubt, run the read‑only commands and escalate.

---

## 1. What the SRE Agent does

It is a command‑line tool that **monitors and manages** our Proxmox and Hetzner
infrastructure with **human‑in‑the‑loop approvals**. As junior support you will
mostly use it to:

- check the health of nodes, VMs, and services,
- look up the status of a specific VM,
- see what exists (inventory) and whether anything has drifted,
- create an approval request for an action and hand it to a senior for approval.

You run it with the command **`sre-agent`**.

---

## 2. Before you start (one‑time)

These are normally **already set up** on the support workstation. If a command
fails with "command not found" or "config", stop and ask an SRE — do not try to
install credentials yourself.

| Check | Command | Expected |
|---|---|---|
| Tool is installed | `sre-agent --help` | prints the list of commands |
| It can reach infra | `sre-agent health` | shows connectors as healthy |

- Python 3.11+ and the package are pre‑installed (`pipx`/venv per workstation).
- Configuration (which nodes, which tokens, which Slack channel) lives in a
  config file the SRE team manages. **You never edit credentials.**
- If you need a specific environment, an SRE will tell you to add `--profile`
  (e.g. `--profile production`). Otherwise leave it off.

---

## 3. Safe commands you can run anytime (read‑only)

These never change anything. Use them freely.

```bash
sre-agent health                 # are the connectors/targets reachable?
sre-agent monitor                # health + workload of nodes, VMs, services
sre-agent monitor -o json        # same, machine-readable
sre-agent list-nodes             # Proxmox nodes
sre-agent list-vms               # all VMs / containers
sre-agent status --node srv1 --vmid 100      # one VM's status
sre-agent discover               # full inventory across connectors
sre-agent graph                  # the resource graph (what exists)
sre-agent drift                  # what differs from desired state
sre-agent metrics                # uptime/SLO + counters (Prometheus format)
sre-agent audit                  # what the agent has done (audit log)
```

### Reading `monitor` output

Each line is a check with a **status**:

| Status | Meaning | What you do |
|---|---|---|
| `OK` | healthy | nothing |
| `WARN` (degraded) | high CPU/memory/disk, slow response | note it; escalate if it persists or trends up |
| `DOWN` / `ERR` | unreachable or failing | escalate to SRE immediately; capture the line |

The last line is a summary: `Total / OK / WARN / DOWN`. If anything is `DOWN`,
copy the whole output into the incident channel and tag an SRE.

---

## 4. Running an operation (and the approval workflow)

Operations are tiered. You can run **auto** and **notify** ones directly; the
agent will **block approve / pr_only** ones and tell you so.

### Run a single operation

```bash
sre-agent run --operation restart_vm --target srv1:100 \
    --payload '{"node":"srv1","vmid":"100"}'
```

- If it prints a JSON result → it ran (it was an auto/notify operation).
- If it prints **"requires approval"** → it's a risky operation. **Do not try to
  force it.** Create an approval batch instead (next section) and get a senior to
  approve.

### The approval workflow (for risky actions)

Risky actions (deleting a VM, network/firewall changes, creating servers, etc.)
go through a batch that a **senior approves**:

```bash
# 1. Create the batch (this does NOT run the action — it queues it for approval)
sre-agent batch create --operation delete_vm --target srv1:100 \
    --payload '{"node":"srv1","vmid":"100"}'

# 2. See what's queued
sre-agent batch list

# 3. A SENIOR approves it (their name is recorded in the audit log)
sre-agent batch approve --action-id <id-from-list> --approver senior@altoros.com

# 4. Or decline it
sre-agent batch decline --action-id all
```

- Creating a batch sends a notification (Slack/email) to the approvers.
- **Approval is recorded with who approved it.** Use the real approver's identity.
- **The approver must be on the allow-list** (`approvals.authorized_approvers` in
  config). Anyone not listed is rejected — and by default you **cannot approve a
  batch you created yourself** (separation of duties). If approve says it was
  rejected, you're either not authorized or it's your own request — get another
  authorized engineer to approve.
- If no approval channel is reachable, the agent **fails closed** — the batch will
  not run, and you'll see a warning. That's by design; escalate.

### Skills (multi‑step workflows)

A "skill" plans a routine workflow into several actions that still go through the
same approval gate:

```bash
sre-agent skill --name rolling_restart \
    --params '{"vms":[{"node":"srv1","vmid":"100"},{"node":"srv1","vmid":"101"}]}'
```

It will list the planned actions and send them for approval. Hand off to a senior
to approve.

---

## 5. What junior support should NOT do

🚫 **Do not** run `sre-agent emergency …`. Emergency mode bypasses approval for a
small allow‑list of actions and is for on‑call SREs during a live incident only.

🚫 **Do not** look for ways to "force" or skip an approval. If the agent blocks
something, that is the correct behavior — escalate.

🚫 **Do not** edit the config file, tokens, or secrets. If creds look wrong, tell
an SRE.

🚫 **Do not** approve your own risky batches. Approval is a second pair of eyes —
a senior approves.

🚫 **Do not** run `remediate` (it proposes infrastructure changes) without an SRE
asking you to.

✅ **Do** freely use every command in Section 3. ✅ **Do** create approval batches
and hand them off. ✅ **Do** capture and escalate anything `DOWN`/`ERR`.

---

## 6. Exit codes (for scripts / knowing if it worked)

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | the operation failed or was blocked (read the message) |
| `2` | you used the command wrong (bad option / bad `--payload` JSON) |

Check with `echo $?` right after a command.

---

## 7. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `command not found: sre-agent` | not installed / wrong shell | open a fresh terminal; if still missing, ask an SRE |
| `Failed to initialize coordinator` | config missing/broken | don't edit it — escalate to SRE |
| `Invalid --payload JSON` (exit 2) | the `--payload` text isn't valid JSON | check quotes/braces; payload must be like `'{"node":"srv1","vmid":"100"}'` |
| a check shows `tls_verification_failed` | certificate problem on a target | **escalate** — never disable TLS verification yourself |
| `requires approval` | normal — risky op | use the batch workflow + senior approval |
| `no approval channel succeeded … fail-closed` | Slack/email down | escalate; the action correctly did **not** run |
| something shows `DOWN` / `ERR` in monitor | a target is unhealthy | capture output, post to incident channel, tag SRE |

When you escalate, include: the **exact command** you ran, the **full output**,
and the **time**.

---

## 8. Quick reference (cheat sheet)

```bash
# Safe / read-only (run anytime)
sre-agent health
sre-agent monitor
sre-agent list-nodes
sre-agent list-vms
sre-agent status --node <node> --vmid <id>
sre-agent discover
sre-agent drift
sre-agent audit

# Routine ops (auto/notify run directly; risky ones get blocked)
sre-agent run --operation <op> --target <t> --payload '<json>'

# Approval workflow (you queue, a senior approves)
sre-agent batch create --operation <op> --target <t> --payload '<json>'
sre-agent batch list
sre-agent batch approve --action-id <id> --approver <you@altoros.com>
sre-agent batch decline --action-id all

# DO NOT USE without an SRE: emergency, remediate, --auto-approve, --force
```

---

## 9. Escalation

- **Anything `DOWN`/`ERR`, a TLS error, or a failed approval channel →** escalate
  to the on‑call SRE immediately (incident channel).
- **Anything that asks for approval →** hand the batch ID to a senior; don't force it.
- **Unsure?** Run the read‑only commands, capture output, and ask. Reading never
  breaks anything.

_Maintained by the SRE / Helpdesk team. If a command in this guide doesn't match
what you see, tell an SRE — the tool may have been updated._
