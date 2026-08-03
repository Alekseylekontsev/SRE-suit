"""Coordinator — the public orchestrator that wires the control plane together.

This is the environment-agnostic entry point used by the CLI (and, later, an
API). It composes: typed config → secrets provider → connector registry →
policy hooks → deterministic classifier → executor → approval manager → audit.
The reasoning layer (Phase 3) plugs in here as a *proposer* that emits Actions
which re-enter this same gate; it is never on the safety path.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from .approvals.manager import ApprovalManager
from .changecontrol.base import FileChangeControlBackend
from .changecontrol.gitlab_mr import GitLabMRBackend
from .config import _ensure_defaults, load_config
from .connectors.registry import build_from_config
from .core.audit import AuditLog
from .core.batch import BatchRunner
from .core.classification import Classifier
from .core.executor import Executor
from .core.models import Action, AutonomyTier, Batch, OperationResult
from .dataplane.drift import DesiredState, drift_to_changesets
from .dataplane.drift import detect_drift as _detect_drift
from .dataplane.graph import build_from_coordinator
from .hooks.builtin.destructive_guard import DestructiveGuard
from .hooks.builtin.secret_scrub import SecretScrub
from .hooks.registry import HookRegistry
from .observability.metrics import MetricsRegistry
from .observability.tracing import get_tracer
from .secrets.keys import load_kek
from .secrets.providers import EnvFileProvider
from .secrets.store import EncryptedSecretStore
from .skills.base import SkillContext
from .skills.registry import default_registry


def _build_secrets(config: Dict):
    """Pick a secrets provider from config: an encrypted store when a key
    source is configured, otherwise the dev env/file provider."""
    sec = config.get("secrets", {}) or {}
    store_cfg = sec.get("store")
    if store_cfg and store_cfg.get("path") and sec.get("kek"):
        return EncryptedSecretStore(store_cfg["path"], load_kek(sec["kek"]))
    return EnvFileProvider(path=sec.get("env_file"), prefix=sec.get("prefix", "SRE_SECRET_"))


def _resolve_secret_refs(config: Dict, secrets, audit) -> Dict:
    """Resolve connector credential *references* through the secrets provider.

    A connector can reference a secret by name (``token_secret``) instead of
    inlining a raw ``token``; this resolves those refs via the provider so the
    encrypted store / env provider / Bitwarden is the live credential source.
    Raw ``token`` still works (back-compat). Resolution is audited by key only
    (never the value). Returns a deep-copied, resolved config.
    """
    import copy
    cfg = copy.deepcopy(config)

    def _resolve(holder: Dict, label: str) -> None:
        ref = holder.get("token_secret")
        if not ref or holder.get("token"):
            return
        value = secrets.get(ref)
        if value:
            holder["token"] = value
            audit("secret_resolved", {"target": label, "key": ref})
        else:
            audit("secret_unresolved", {"target": label, "key": ref})

    for node in (cfg.get("proxmox", {}) or {}).get("nodes", []) or []:
        _resolve(node, f"proxmox:{node.get('name', node.get('host', '?'))}")
    if isinstance(cfg.get("hetzner"), dict):
        _resolve(cfg["hetzner"], "hetzner")
    return cfg


def _build_changecontrol(config: Dict):
    """Pick a change-control backend for PR_ONLY ops. None → PR_ONLY ops are
    blocked with a clear message until a backend is configured."""
    cc = config.get("changecontrol", {}) or {}
    if cc.get("backend") == "gitlab":
        gl = config.get("gitlab", {}) or {}
        if gl.get("url") and gl.get("token") and gl.get("project"):
            return GitLabMRBackend(gl["url"], gl["project"], gl["token"],
                                   target_branch=cc.get("target_branch", "main"),
                                   changes_dir=cc.get("changes_dir", "changesets"))
    if cc.get("dir"):
        return FileChangeControlBackend(cc["dir"])
    return None


class Coordinator:
    def __init__(self, config: Optional[Dict] = None, profile: Optional[str] = None,
                 config_path: Optional[str] = None):
        if config is None:
            config = load_config(path=config_path, profile=profile)
        else:
            config = _ensure_defaults(config)
        self.config = config

        self.audit = AuditLog()
        self.hooks = HookRegistry()
        self.hooks.register(DestructiveGuard())
        self.hooks.register(SecretScrub())

        self.secrets = _build_secrets(config)
        self.changecontrol = _build_changecontrol(config)
        # Resolve any `token_secret` references through the provider so the
        # secrets store is the real credential source when configured.
        resolved = _resolve_secret_refs(config, self.secrets, self.audit)
        self.registry = build_from_config(resolved)
        self.classifier = Classifier(config)
        self.executor = Executor(self.registry, self.classifier, self.hooks,
                                 secrets=self.secrets, audit=self.audit,
                                 changecontrol=self.changecontrol)
        self.batch_runner = BatchRunner(self.executor, audit=self.audit)
        self.approvals = ApprovalManager(config, audit=self.audit)
        self.skills = default_registry()
        self.metrics = MetricsRegistry()
        self.tracer = get_tracer()
        self.batch: Optional[Batch] = None

    # ---- classification ----------------------------------------------------
    def classify(self, operation: str) -> AutonomyTier:
        spec = self.registry.spec_for(operation)
        return self.classifier.classify(operation, intrinsic=getattr(spec, "tier", None))

    # ---- single action -----------------------------------------------------
    def execute(self, operation: str, target: str = "", payload: Optional[Dict] = None,
                emergency: bool = False, force: bool = False) -> OperationResult:
        action = Action(id=f"action_{int(time.time())}", name=operation,
                        target=target or "unknown", payload=payload or {})
        with self.tracer.span("action.execute", operation=operation, target=target):
            result = self.executor.execute(action, emergency=emergency, force=force)
        self.metrics.record_action(operation, result.success)
        return result

    # ---- batch + approval --------------------------------------------------
    def submit_batch(self, actions: List[Action], batch_id: Optional[str] = None,
                     window_minutes: Optional[int] = None, auto_approve: bool = False) -> Batch:
        window = window_minutes or self.config.get("approvals", {}).get("batch_window_minutes", 180)
        batch = self.batch_runner.build_batch(
            batch_id or f"BATCH-{int(time.time())}", actions, window_minutes=window)
        self.batch = batch
        if auto_approve:
            for a in batch.actions:
                a.status = "APPROVED"
                a.approver = "auto"
            self.batch_runner.run_batch(batch)
        return batch

    def trigger_approvals(self, batch: Batch) -> bool:
        """Fail-closed: returns False if no channel succeeded — caller must not run."""
        return self.approvals.trigger(batch)

    def approve(self, action_id: str, approver: str) -> List[OperationResult]:
        """Approve matching action(s) in the active batch and run them."""
        if self.batch is None:
            return []
        if self.approvals.approve(self.batch, action_id, approver):
            return self.batch_runner.run_batch(self.batch)
        return []

    def run_approved(self, emergency: bool = False) -> List[OperationResult]:
        if self.batch is None:
            return []
        return self.batch_runner.run_batch(self.batch, emergency=emergency)

    # ---- read paths --------------------------------------------------------
    def discover(self) -> Dict[str, object]:
        result: Dict[str, object] = {}
        for op in ("list_nodes", "list_vms", "hetzner_list_servers"):
            if self.registry.spec_for(op) is not None:
                r = self.execute(op)
                result[op] = r.result if r.success else {"error": r.error}
        return result

    # ---- data plane --------------------------------------------------------
    def resource_graph(self):
        """Build the resource graph (single source of truth for what exists)."""
        return build_from_coordinator(self)

    def detect_drift(self, closed_world: bool = False):
        """Compare desired state (config) against the live resource graph."""
        desired = DesiredState.from_config(self.config)
        findings = _detect_drift(desired, self.resource_graph(), closed_world=closed_world)
        self.audit("drift_detected", {"findings": len(findings)})
        return findings

    def remediate_drift(self, closed_world: bool = False):
        """Propose remediation change-sets for detected drift via change control.

        Returns ChangeRequests when a backend is configured, else the raw
        ChangeSets (proposals only — nothing is executed directly)."""
        changesets = drift_to_changesets(self.detect_drift(closed_world=closed_world))
        if self.changecontrol is None:
            return changesets
        requests = [self.changecontrol.propose(cs) for cs in changesets]
        self.audit("drift_remediation_proposed", {"count": len(requests)})
        return requests

    def health(self) -> Dict[str, object]:
        return {name: vars(h) for name, h in self.registry.health().items()}

    def monitor(self, output: str = "table") -> str:
        # Monitoring reads through the same hardened connector clients used for
        # everything else (each BaseHTTPClient exposes a `_request` shim).
        from .monitoring.checks import format_results
        from .monitoring.runner import run_platform_checks

        clients: Dict[str, object] = {}
        for connector in self.registry.connectors():
            if hasattr(connector, "read_clients"):
                clients.update(connector.read_clients())
        results = run_platform_checks(self.config, clients)
        return format_results(results, output=output)

    def audit_log(self) -> List[Dict]:
        return self.audit.entries()

    # ---- skills (workflows) ------------------------------------------------
    def run_skill(self, name: str, params: Optional[Dict] = None,
                  auto_approve: bool = False) -> Batch:
        """Plan a skill into Actions and submit them as a batch through the gate.

        The skill only *plans* (pure, no side effects); every Action it produces
        re-enters the deterministic classify → approve → execute path.
        """
        ctx = SkillContext(params=params or {}, config=self.config)
        actions = self.skills.plan(name, ctx)
        self.audit("skill_planned", {"skill": name, "actions": len(actions)})
        return self.submit_batch(actions, batch_id=f"SKILL-{name}", auto_approve=auto_approve)

    # ---- reasoning (optional LLM proposer) ---------------------------------
    def propose_and_submit(self, goal: str, proposer=None,
                           context: Optional[Dict] = None) -> Batch:
        """Use the optional reasoning layer to PROPOSE Actions, then submit them
        as a batch — proposed actions are never auto-approved; they pass through
        the same approval gate as any other action (design principle P1).

        ``proposer`` is injected for testing; if None, the Claude proposer is
        lazy-constructed (requires the anthropic SDK). The reasoning module is
        imported lazily so the safety path never depends on it.
        """
        if proposer is None:
            from .reasoning.claude import ClaudeProposer
            proposer = ClaudeProposer()
        proposal = proposer.propose(goal, context)
        self.audit("actions_proposed",
                   {"goal": goal, "count": len(proposal.actions), "rationale": proposal.rationale})
        return self.submit_batch(proposal.actions, batch_id="PROPOSED", auto_approve=False)


__all__ = ["Coordinator"]
