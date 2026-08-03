# Running the SRE Agent in a Container

A pre-built image bundles the `sre-agent` CLI and **all dependencies** — no local
Python install needed. The agent is a CLI tool (not a daemon), so you run it
on demand.

## What's in the image

- `sre-agent` CLI + core deps (`cryptography`) and the optional extras
  (`yaml`, `tracing`, `reasoning`).
- Runs as a **non-root** user (`sre`, uid 10001).
- **No secrets/config baked in** — you mount `config.yaml` read-only at runtime.
- Hardened: read-only root filesystem, `no-new-privileges`, all Linux
  capabilities dropped; only a dedicated data volume is writable (for the
  encrypted secret store and audit exports).

## Prerequisites

- Docker (with Compose v2).
- A `config.yaml` for your environment. **Ask an SRE for it** — it contains
  credentials. Put it at `./config.yaml` next to `docker-compose.yml`.
  A template is in the repo at `sre_agent/config/profiles/config.example.yaml`.

## Build once

```bash
docker compose build
# or without compose:
docker build -t sre-agent:latest .
```

## Run commands (Compose — recommended)

`docker compose run --rm` runs one command and cleans up the container:

```bash
docker compose run --rm sre-agent health
docker compose run --rm sre-agent monitor
docker compose run --rm sre-agent list-vms
docker compose run --rm sre-agent status --node srv1 --vmid 100
docker compose run --rm sre-agent batch create \
    --operation delete_vm --target srv1:100 \
    --payload '{"node":"srv1","vmid":"100"}'
```

The entrypoint auto-passes the mounted `--config /config/config.yaml`, so you
don't repeat it. To use a different profile, add `--profile production` etc.

## Run commands (plain `docker run`)

```bash
docker run --rm \
  --read-only --security-opt no-new-privileges:true --cap-drop ALL \
  --tmpfs /tmp \
  -v "$PWD/config.yaml:/config/config.yaml:ro" \
  -v sre-data:/var/lib/sre-agent \
  sre-agent:latest health
```

## Networking (reaching internal services)

By default the container uses Docker's **bridge** network, which can reach public
endpoints (Proxmox public IPs, GitLab) but **not** services on an internal VLAN
(e.g. LiteLLM on an internal IP like `172.22.50.33`). If `monitor` shows those as
`DOWN: unreachable` while the VM itself is "running", it's a routing issue, not an
outage. To reach internal-only targets, use **host networking**:

```bash
# wrapper:
SRE_NETWORK=host ./bin/sre-agent monitor
# plain docker run: add --network host
# compose: uncomment `network_mode: host` in docker-compose.yml
```

Host networking removes container network isolation — enable it only when you
actually need internal targets.

## Security notes

- **Never bake `config.yaml` into the image.** It is excluded via `.dockerignore`
  and mounted read-only at runtime. Rotate any token that leaks.
- The encrypted secret store and audit log persist in the **`sre-data`** volume.
  Back it up if you rely on stored secrets/audit history; treat it as sensitive.
- The optional LLM reasoning layer needs `ANTHROPIC_API_KEY` — pass it via the
  environment (see the commented block in `docker-compose.yml`), never bake it in.
- Pin the base image by digest in CI for full supply-chain control (the
  Dockerfile pins by tag for readability).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `config ... not found` / init error | ensure `./config.yaml` exists and is mounted; ask an SRE for the file |
| `permission denied` writing secrets | make sure the `sre-data` volume is used (don't run `--read-only` without it) |
| healthcheck failing | run `docker compose run --rm sre-agent health` and read the error; likely connectivity/credentials |

For what the commands mean and the approval workflow, see
[`admin-guide.md`](admin-guide.md).
