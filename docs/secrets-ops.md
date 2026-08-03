# Secrets Operations — Bitwarden (Teams plan)

How the on-call team stores, fetches, and rotates the SRE Agent's credentials
**without** copying them to each laptop — so rotating once updates everyone.

**Cost:** uses Bitwarden **Teams** ($36/yr) only. No Secrets Manager add-on needed.

## Model

One shared **org Collection** holds the agent's `config.yaml` as a single Secure
Note = the **one source of truth**. Each engineer runs the agent locally on their
own laptop, authenticates to Bitwarden **as themselves**, and the wrapper fetches
the config at run time into a temp file that's deleted afterwards. Nothing is
stored on the laptop.

```
Bitwarden org Collection "SRE Agent"  ──(bw get, per-user auth)──▶  each laptop (RAM/tmp only)
                  ▲ rotate here once                                   discards after run
```

## One-time setup (admin)

1. Bitwarden web → **Collections** → create **"SRE Agent"**; share it with the
   on-call **group**.
2. Add an **item → Secure Note**, name it exactly e.g. `SRE Agent config`, and
   paste the full `config.yaml` contents into the note body. Assign it to the
   "SRE Agent" Collection.
3. Add/remove engineers by Collection/group membership (this is also how you
   **offboard** — remove them and they instantly lose access).

## Install the Bitwarden CLI (`bw`) — no Homebrew needed

```bash
npm install -g @bitwarden/cli          # if you have Node
# or download the standalone binary from https://bitwarden.com/help/cli/ (unzip, put `bw` on PATH)
```

## Per engineer, once per work session

```bash
bw config server https://vault.bitwarden.com   # or your self-host URL
bw login                                        # your own account + MFA
export BW_SESSION="$(bw unlock --raw)"          # unlock; session lives in this shell
```

## Daily use

```bash
SRE_BW_ITEM="SRE Agent config" ./bin/sre-agent health
SRE_BW_ITEM="SRE Agent config" ./bin/sre-agent monitor
# internal-only targets (e.g. LiteLLM): add host networking
SRE_NETWORK=host SRE_BW_ITEM="SRE Agent config" ./bin/sre-agent monitor
```

Tip: `export SRE_BW_ITEM="SRE Agent config"` once in your shell so you can just
run `./bin/sre-agent monitor`.

## Rotating a credential (the whole point)

When the on-shift engineer rotates a token (GitLab / Proxmox / Hetzner):

1. In the provider's UI: create the new token (least scope), **revoke the old**.
2. In Bitwarden: open the **`SRE Agent config`** note, update that token value, save.
3. Verify: `SRE_BW_ITEM="SRE Agent config" ./bin/sre-agent health`.
4. **Everyone else: nothing to do** — their next run fetches the new value.

No more "one engineer rotates, the rest get auth errors" — there's a single copy.

## Alternative: reference individual tokens (encrypted store / env)

Instead of fetching the whole config, a connector can reference a single token
by name and the coordinator resolves it through the secrets provider at startup:

```yaml
proxmox:
  nodes:
    - name: srv1
      host: https://...:8006
      token_secret: proxmox_srv1     # resolved via the provider, not inlined
```

- With the **env provider** (default), that resolves `SRE_SECRET_PROXMOX_SRV1`.
- With an **encrypted store** (`secrets.store.path` + `secrets.kek` configured),
  it resolves from the AES-GCM store on disk.

Resolution is audited by key name (never the value); a missing secret is logged
as `secret_unresolved` and that connector simply isn't built. Raw inline `token`
still works and takes precedence.

## Security notes

- Secrets never persist on a laptop — fetched to a `0600` temp file, deleted on exit.
- Access = Collection membership; revoke to offboard. (Detailed per-fetch event
  logs are Enterprise-only in Bitwarden; the agent's own audit log records actions.)
- Keep `BW_SESSION` to your shell; it clears when the shell closes or on `bw lock`.
- This replaces the local plaintext `config.yaml` and the on-disk encrypted store
  for this deployment — Bitwarden is the source of truth.
