# SRE Agent — Platform Architecture Design

- **Date:** 2026-06-12
- **Status:** Approved design (full platform); implementation sequenced in phases
- **Repo:** `admins/hetzner_proxmox` (local `/Users/alekslek/Documents/lekontsev/opencode`)
- **Supersedes:** the monolithic `agent.py` orchestrator + ad-hoc handler registration

## 1. Vision & success criteria

The SRE Agent is a **scalable, environment-agnostic coordinator** whose mission is to **monitor and continuously improve client infrastructure**. It is modular and configurable per environment, extends through **connectors / skills / hooks / subagents**, and acts as a **secrets-delivery provider** to the systems it drives.

Success criteria:

1. **Reliability:** support a ≥ 99.99% uptime objective for managed infrastructure (≤ ~52 min/yr) through continuous monitoring, drift detection, and safe automated remediation.
2. **Security:** top-tier, continuously improving posture — fail-closed by default, no secret ever logged or persisted in plaintext outside the encrypted store, every privileged action gated and audited.
3. **Extensibility:** new platforms, workflows, and policies are added without modifying the core.

## 2. Foundational principles

- **P1 — Deterministic safety path.** Operation classification, approval gating, secret access, and audit are pure-Python and **never depend on an LLM**. The optional reasoning layer may *propose* actions, but every action funnels through the same deterministic gate.
- **P2 — Fail-closed, structurally.** Any ambiguity, channel failure, expired window, or unknown operation denies the action. Fail-safe is enforced by control flow, not convention.
- **P3 — Least privilege & JIT secrets.** Credentials are leased with short TTLs and injected at call time; never handed to sandboxes or persisted long-lived.
- **P4 — Observability is not optional.** Every tool call, credential request, and decision point is traced and audited.
- **P5 — Change control before mutation.** Risky changes prefer a diff → PR → CI → human-approval → apply path over direct control-plane mutation.
- **P6 — Modular isolation.** Each unit has one purpose, a typed interface, and is testable in isolation. Connectors/skills are runnable in restricted/ephemeral contexts.

## 3. Six-plane model

Adapted from the cloudgeni-ai infrastructure-agents guide:

| Plane | Responsibility | Modules |
|------|----------------|---------|
| 1. Orchestration | Intent → classify → gate → dispatch → audit | `coordinator`, `core/` |
| 2. Tools / Skills | Operations, workflows, MCP tools as peers | `connectors/`, `skills/` |
| 3. Sandboxed execution | Run connectors/skills with restricted privilege | execution context in `core/executor` |
| 4. Credentials | Encrypted store + JIT leasing | `secrets/` |
| 5. Data plane | Resource graph + drift (desired vs actual) | `dataplane/` |
| 6. Change control | GitOps PR path for risky ops | `changecontrol/` |

Cross-cutting: `hooks/` (policy at decision points), `observability/` (metrics + tracing), `config/` (typed configuration), `reasoning/` (optional LLM proposer).

## 4. Target package structure

```
sre_agent/
├── cli.py                  # thin argparse → coordinator
├── coordinator.py          # orchestrator
├── core/
│   ├── models.py           # Action, Batch, OperationResult, AutonomyTier, OperationSpec
│   ├── classification.py   # operation → AutonomyTier (deterministic)
│   ├── executor.py         # resolves connector+op; runs in direct|gitops mode; applies hooks
│   ├── batch.py            # batch lifecycle + approval window/expiry + signals
│   ├── audit.py            # structured, secret-scrubbed audit trail
│   └── errors.py           # typed exceptions
├── config/
│   ├── schema.py           # typed dataclasses + validation
│   ├── loader.py           # load/merge/env-override (hardened)
│   └── profiles/*.yaml
├── connectors/
│   ├── base.py             # Connector ABC
│   ├── registry.py         # config- + entry-point discovery
│   ├── http.py             # shared hardened BaseHTTPClient (TLS, retry, encoding)
│   ├── proxmox.py · hetzner.py
├── secrets/
│   ├── provider.py         # SecretsProvider ABC (get/put/delete/lease)
│   ├── store.py            # AES-GCM envelope encrypted-at-rest store
│   ├── keys.py             # KEK sources: passphrase(KDF) | env | KMS hook
│   └── providers.py        # EnvFileProvider (dev), ExternalBackendProvider hook (Vault)
├── dataplane/
│   ├── graph.py            # resource/inventory model across connectors
│   └── drift.py            # desired-vs-actual drift detection
├── approvals/
│   ├── base.py             # ApprovalChannel ABC (fail-closed contract)
│   ├── manager.py          # trigger + enforce window + record approver identity
│   └── channels/{slack,gmail}.py
├── monitoring/  checks.py · runner.py
├── skills/      base.py · builtin/…
├── hooks/       base.py · registry.py · builtin/{destructive_guard,secret_scrub,rate_limit}
├── changecontrol/ base.py · gitlab_mr.py
├── reasoning/   sdk_agent.py        # OPTIONAL, lazy-imported
└── observability/ metrics.py · tracing.py
```

## 5. Autonomy tiers (replaces flat read/write/approval/emergency)

`AutonomyTier` (enum), assigned per operation in config and/or by the operation's `OperationSpec`:

| Tier | Meaning | Gate behavior |
|------|---------|---------------|
| `AUTO` | Read-only / explicitly safe | Executes immediately; audited. |
| `NOTIFY` | Low-risk writes | Executes, but emits a notification (non-blocking). |
| `APPROVE` | Risky/destructive | Requires human approval via a channel; fail-closed. |
| `PR_ONLY` | High-blast-radius / declarative | Must go through `changecontrol` (diff→PR→CI→merge); no direct mutation. |

- **Default for unknown operations = `APPROVE`** (fail-closed).
- **Emergency mode** is a runtime override, not a tier: it may downgrade a *specific allow-listed* op to `AUTO`, validated at load time that the emergency allow-list ∩ destructive ops is intentional, with a distinct audit event. Every emergency op must have a registered handler (startup assertion).
- Config must not place one operation in conflicting tiers; the **more restrictive tier wins** and a load-time validation warns on overlap.

## 6. Key interfaces (ABCs)

```python
# connectors/base.py
class Connector(ABC):
    name: str
    @abstractmethod
    def operations(self) -> dict[str, "OperationSpec"]: ...      # name → spec(callable, tier, idempotent)
    @abstractmethod
    def capabilities(self) -> set[str]: ...
    @abstractmethod
    def health(self) -> "HealthStatus": ...                       # feeds monitoring + SLO

# secrets/provider.py
class SecretsProvider(ABC):
    @abstractmethod
    def get(self, key: str) -> str | None: ...
    @abstractmethod
    def put(self, key: str, value: str) -> None: ...
    @abstractmethod
    def delete(self, key: str) -> None: ...
    @abstractmethod
    def lease(self, key: str, ttl_seconds: int) -> "Lease": ...   # short-TTL, JIT injection

# hooks/base.py — synchronous policy hooks at decision points
class Hook(ABC):
    points: set[HookPoint]   # PRE_ACTION, POST_ACTION, PRE_APPROVAL, ON_AUDIT
    @abstractmethod
    def run(self, point: HookPoint, ctx: "HookContext") -> "HookOutcome": ...  # ALLOW | DENY(reason) | MUTATE

# skills/base.py — higher-level workflows composed of operations
class Skill(ABC):
    name: str
    @abstractmethod
    def plan(self, ctx: "SkillContext") -> list[Action]: ...      # returns Actions; each re-enters the gate

# changecontrol/base.py
class ChangeControlBackend(ABC):
    @abstractmethod
    def propose(self, change: "ChangeSet") -> "ChangeRequest": ... # e.g. open GitLab MR with diff + CI
```

A connector operation is dispatched through `executor`, which: (1) resolves the `OperationSpec`, (2) runs `PRE_ACTION` hooks, (3) for `PR_ONLY` routes to `changecontrol`, else (4) leases required secrets JIT, (5) executes in the chosen mode, (6) runs `POST_ACTION` hooks, (7) writes a scrubbed audit + trace span. MCP-backed tools and native connectors are peers in the registry (capability catalog).

## 7. Secrets security model (built-in encrypted store)

- **Crypto:** envelope encryption using the vetted `cryptography` library (AES-256-GCM), never hand-rolled primitives. Per-secret data-encryption key (DEK) wrapped by a key-encryption key (KEK). (Google Tink is an accepted alternative if multi-language parity is needed.)
- **KEK sources (pluggable, `keys.py`):**
  1. Passphrase → KEK via a memory-hard KDF (scrypt via `cryptography`); salt stored alongside.
  2. Raw 32-byte key from env / mounted file (base64).
  3. External KMS hook (AWS KMS / Vault transit) — wrap/unwrap the KEK without it touching disk.
- **At-rest format:** versioned JSON envelope `{v, kdf, salt, nonce, wrapped_dek, ciphertext, aad}`; file created `0600`.
- **JIT leasing:** `lease()` returns a short-TTL `Lease`; the executor injects the secret into the connector call and discards it after; leases are audited (key id + requester + ttl, never the value).
- **Threat model & mitigations:**
  - *Disk theft* → ciphertext only; KEK not co-located when KMS/passphrase used.
  - *Process memory scrape* → minimize plaintext lifetime; no global plaintext caching; zero-after-use best effort.
  - *Log/audit leakage* → mandatory `secret_scrub` hook + audit serializer redaction; tests assert no secret material in audit/log output.
  - *Privilege via emergency/force* → secret access still tiered and audited; `force` requires explicit allow-list, emits `forced_execution` audit event.
- **Non-goals:** not a general KMS, no multi-tenant key isolation in Phase 1, no HSM (KMS hook covers the upgrade path).

## 8. Data flow

**Direct mode (AUTO/NOTIFY/APPROVE):**
```
intent → coordinator → classify(tier) → PRE_ACTION hooks
  → [APPROVE? batch + approval manager (fail-closed, window, approver id)]
  → lease secrets (JIT) → connector.operation() → POST_ACTION hooks
  → scrubbed audit + OTel span → result
```

**GitOps mode (PR_ONLY):**
```
intent → classify(PR_ONLY) → executor builds ChangeSet (diff vs dataplane desired state)
  → changecontrol.propose() opens MR → CI validates → human merges → apply on merge event
```

**Fail-closed approval contract:** `ApprovalChannel.notify()` must return a definite success/failure. If **all** enabled channels fail, the batch is **not** left approvable and the coordinator denies execution. Approval flips to `APPROVED` only with a recorded approver identity and within the window; expired windows are rejected (replay protection). `run_batch` executes `APPROVED` actions with the gate satisfied (the bug fixed in the prior hardening pass).

## 9. Data plane

- `graph.py`: a normalized resource model (nodes, VMs, volumes, networks, servers) populated from connectors via discovery; the single source of truth for "what exists."
- `drift.py`: compares desired state (config / IaC repo) against the live graph; surfaces drift as findings that can feed monitoring alerts or `PR_ONLY` remediation change-sets.
- Phase 1 ships the graph model + discovery population; drift detection lands in Phase 2 alongside GitOps.

## 10. Observability & SLO

- `metrics.py`: counters/gauges for action outcomes, approval latency, connector health, and an **uptime/SLO tracker** (per-target availability windows toward the 99.99% objective). Optional Prometheus exposition.
- `tracing.py`: OpenTelemetry spans around every connector call, credential lease, classification, approval, and change-request — **optional dependency**, no-op tracer when not installed (core never hard-depends on OTel).
- Audit log remains the security-of-record; tracing is the operational view.

## 11. Durable workflow lifecycle

The batch + approval lifecycle is modeled as a workflow with **approval signals** and **non-retryable side-effect activities** (no auto-retry of non-idempotent ops — consistent with the client retry policy already fixed).

- Phase 1/2: a synchronous, in-process `WorkflowEngine` default implementation; state persisted to disk for crash-restart.
- Later (optional): a Temporal (Python SDK) backend implementing the same interface for long-running/distributed durability. **No Temporal dependency is introduced in this design's funded phases.**

## 12. Dependencies & compatibility

- **Core stays import-light.** Required new dependency: `cryptography` (secrets store) — declared so the store raises a clear, actionable error if absent.
- **Optional extras:** `PyYAML` (already optional), `opentelemetry-sdk` (`[tracing]`), Claude Agent SDK (`[reasoning]`), Temporal (`[durable]`, future).
- Python ≥ 3.11 (unchanged). The hardened `connectors/http.py` consolidates the TLS/retry/encoding logic from the current Proxmox/Hetzner clients (fail-closed TLS, https-only, no-retry-on-4xx, path/query encoding).

## 13. Testing strategy

- **Unit-per-module**, isolation via the typed interfaces. Each ABC gets a fake/in-memory implementation for testing consumers.
- **Security tests (mandatory):** fail-closed approval on channel failure; window/expiry rejection; unknown-op → APPROVE; emergency allow-list enforcement; no secret material in audit/log/trace output; TLS fail-closed; https-only connectors.
- **Determinism test:** the safety path executes with the reasoning layer absent/disabled.
- **Migration safety:** the existing 71 tests are ported to the new module paths; the suite stays green at each commit. New target ≥ existing coverage; secrets and approvals modules get dedicated suites.
- **Drift/dataplane:** golden-graph fixtures; drift detection asserts known desired-vs-actual deltas.

## 14. Migration & phasing

The end shape is the full structure above. Implementation is sequenced so each commit is reviewable and the suite stays green:

- **Phase 1 — Deterministic spine.** `core/` (models, classification→autonomy tiers, executor, batch, audit), `coordinator`, `connectors/` (base, registry, shared http, proxmox, hetzner migrated), `secrets/` (store + providers + leasing), `hooks/` (base + builtin), `config/` (typed schema + hardened loader), `approvals/` (fail-closed manager + window + approver id), `monitoring/` ported, `cli.py`. Port all existing tests + add security suites.
- **Phase 2 — Knowledge & change control.** `dataplane/` (graph + drift), `changecontrol/` (GitLab MR backend), `PR_ONLY` executor mode, drift→remediation change-sets.
- **Phase 3 — Intelligence & scale.** `skills/` (builtin workflows), `reasoning/` (Claude Agent SDK proposer behind the gate), `observability/tracing.py` (OTel), optional durable-workflow backend.

Old import paths (`sre_agent.agent`, `sre_agent.clients.*`, `sre_agent.config`) retain thin shims during Phase 1 to avoid breaking external callers, removed at the end of Phase 1.

## 15. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Large refactor destabilizes live infra | Phased, suite-green-per-commit; shims for old paths; deterministic core fully tested before cutover. |
| Built-in crypto introduces vulnerabilities | Use `cryptography`/Tink only; documented threat model; KMS hook for KEK; security test suite; no hand-rolled primitives. |
| Scope creep across six planes | Funded phases stop at Phase 3; Temporal/HSM/multi-tenant explicitly deferred (YAGNI). |
| LLM reasoning bypasses safety | P1 enforced structurally — reasoning only emits proposed Actions that re-enter the deterministic gate; determinism test guards it. |
| Secret leakage via logs/traces | Mandatory scrub hook + redacting serializers + assertion tests. |

## 16. Out of scope (this design)

Multi-tenant key isolation, HSM integration, a hosted API/control-plane UI, Temporal deployment, and non-Proxmox/Hetzner connectors (the connector interface makes them additive, but none are built here).
