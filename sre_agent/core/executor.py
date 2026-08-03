"""Single-action executor — the deterministic gate every action passes through.

Order (design principle P2, fail-closed):
  1. resolve the operation spec (unknown → fail)
  2. classify tier (config raised by the spec's intrinsic tier)
  3. PR_ONLY → route to change control (never direct mutation, unless forced)
  4. approval gate — APPROVE blocked unless already approved/forced
     (emergency may downgrade an explicitly allow-listed op)
  5. PRE_ACTION hooks (DENY → fail closed)
  6. JIT-lease declared secrets (audited by key id only)
  7. run the handler
  8. POST_ACTION hooks + scrubbed audit
"""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Optional

from ..changecontrol.base import ChangeItem, ChangeSet
from ..hooks.base import HookContext, HookPoint
from ..hooks.registry import HookRegistry
from .errors import ConnectorError, HookDeniedError, SecretError
from .models import Action, AutonomyTier, OperationResult

_LEASE_TTL_SECONDS = 300


class Executor:
    def __init__(self, registry, classifier, hooks: Optional[HookRegistry] = None,
                 secrets=None, audit=None, changecontrol=None):
        self._registry = registry
        self._classifier = classifier
        self._hooks = hooks or HookRegistry()
        self._secrets = secrets
        self._audit = audit or (lambda *a, **k: None)
        self._changecontrol = changecontrol

    def execute(self, action: Action,
                emergency: bool = False, force: bool = False) -> OperationResult:
        start = time.time()

        def done(success, result=None, error=None):
            return OperationResult(action_id=action.id, success=success, result=result,
                                   error=error, duration_ms=(time.time() - start) * 1000)

        spec = self._registry.spec_for(action.name)
        if spec is None:
            action.status = "FAILED"
            return done(False, error=f"Unknown operation: {action.name}")

        tier = self._classifier.classify(action.name, intrinsic=getattr(spec, "tier", None))

        # --- force: constrained + always audited --------------------------
        # `force` is honored ONLY for ops on the emergency allow-list
        # (security.emergency_auto_actions), and every honored force is audited.
        # A force request for a non-allow-listed op is denied (audited) and
        # falls through to normal gating — it no longer silently bypasses
        # approval or PR_ONLY for arbitrary operations.
        forced = force and self._classifier.is_emergency_allowed(action.name)
        if force and not forced:
            self._audit("forced_execution_denied", {
                "action_id": action.id, "operation": action.name,
                "reason": "operation not in security.emergency_auto_actions",
            })

        # --- PR_ONLY: route to change control unless legitimately forced --
        if tier is AutonomyTier.PR_ONLY and not forced:
            return self._propose_change(action, done)

        # --- approval gate -------------------------------------------------
        # An action counts as approved ONLY if it carries an approver stamp,
        # which only ApprovalManager.approve() sets after authorizing the
        # approver. A bare status="APPROVED" with no approver does NOT pass —
        # provenance is required, so a caller can't self-grant approval.
        gate_satisfied = forced or (action.status == "APPROVED" and bool(action.approver))
        if not gate_satisfied and tier is AutonomyTier.APPROVE:
            if emergency and self._classifier.is_emergency_allowed(action.name):
                pass  # explicit emergency downgrade
            else:
                action.status = "PENDING_APPROVAL"
                return done(False, error=f"Operation '{action.name}' requires approval")

        if forced:
            self._audit("forced_execution", {
                "action_id": action.id, "operation": action.name, "tier": tier.name,
            })

        # --- PRE_ACTION hooks ---------------------------------------------
        try:
            self._hooks.enforce(HookPoint.PRE_ACTION,
                                 HookContext(point=HookPoint.PRE_ACTION, action=action, tier=tier, data={}))
        except HookDeniedError as e:
            action.status = "FAILED"
            self._audit("action_denied", {"action_id": action.id, "operation": action.name, "reason": e.reason})
            return done(False, error=str(e))

        # --- JIT secret leasing (audited by key id only) -------------------
        leases = []
        try:
            for key in getattr(spec, "secret_keys", []) or []:
                if self._secrets is None:
                    raise SecretError(f"operation needs secret {key!r} but no secrets provider is configured")
                leases.append(self._secrets.lease(key, _LEASE_TTL_SECONDS))
                self._audit("secret_leased", {"key": key, "ttl_s": _LEASE_TTL_SECONDS})
        except SecretError as e:
            action.status = "FAILED"
            return done(False, error=str(e))

        # --- execute -------------------------------------------------------
        action.status = "EXECUTING"
        try:
            result = spec.handler(action)
        except ConnectorError as e:
            action.status = "FAILED"
            self._audit("action_failed", {"action_id": action.id, "operation": action.name, "error": str(e)})
            return done(False, error=str(e))
        finally:
            leases.clear()  # discard leased secrets promptly

        action.status = "DONE"
        self._hooks.run_point(HookPoint.POST_ACTION,
                              HookContext(point=HookPoint.POST_ACTION, action=action, tier=tier,
                                          data={"result": result}))
        self._audit("action_executed", {"action_id": action.id, "operation": action.name, "tier": tier.name})
        return done(True, result=result)

    def _propose_change(self, action: Action, done):
        """Route a PR_ONLY action through change control instead of mutating.

        Proposing a reviewable change is the safe path, so PRE_ACTION hooks (e.g.
        destructive_guard) are intentionally not enforced here — review + CI on
        the change request are the gate.
        """
        if self._changecontrol is None:
            action.status = "PENDING_APPROVAL"
            return done(False, error=f"Operation '{action.name}' is PR_ONLY but no "
                                     "change-control backend is configured")
        changeset = ChangeSet(
            id=f"cs-{action.id}",
            title=f"{action.name} on {action.target}",
            description="Proposed by SRE Agent for a PR_ONLY operation.",
            origin="action",
            items=[ChangeItem(operation=action.name, target=action.target,
                              params=dict(action.payload))],
        )
        try:
            cr = self._changecontrol.propose(changeset)
        except Exception as e:  # noqa: BLE001 - a backend failure must not crash the gate
            action.status = "FAILED"
            self._audit("change_proposal_failed",
                        {"action_id": action.id, "operation": action.name, "error": str(e)})
            return done(False, error=f"change control failed: {e}")
        ok = cr.status in ("proposed", "open")
        action.status = "PROPOSED" if ok else "FAILED"
        self._audit("change_proposed",
                    {"action_id": action.id, "operation": action.name,
                     "change_request": asdict(cr)})
        return done(ok, result={"change_request": asdict(cr)},
                    error=None if ok else f"change proposal failed: {cr.detail}")


__all__ = ["Executor"]
