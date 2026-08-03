"""Tests for the sre_agent.hooks package."""
from __future__ import annotations

import pytest

from sre_agent.core.errors import HookDeniedError
from sre_agent.core.models import Action
from sre_agent.hooks import (
    DestructiveGuard,
    Hook,
    HookContext,
    HookDecision,
    HookOutcome,
    HookPoint,
    HookRegistry,
    RateLimit,
    SecretScrub,
    scrub,
)

# --- helpers ----------------------------------------------------------------

def make_action(name="noop", payload=None, status="PENDING"):
    return Action(id="a1", name=name, target="t1", payload=payload or {}, status=status)


class RecordingHook(Hook):
    """Records call order into a shared list and returns a fixed outcome."""

    points = {HookPoint.PRE_ACTION}

    def __init__(self, name, calls, outcome):
        self.name = name
        self._calls = calls
        self._outcome = outcome

    def run(self, ctx):
        self._calls.append(self.name)
        return self._outcome


# --- registry ordering / allow ----------------------------------------------

def test_registry_runs_hooks_in_registration_order():
    calls = []
    reg = HookRegistry()
    reg.register(RecordingHook("first", calls, HookOutcome.allow()))
    reg.register(RecordingHook("second", calls, HookOutcome.allow()))
    reg.register(RecordingHook("third", calls, HookOutcome.allow()))

    ctx = HookContext(point=HookPoint.PRE_ACTION, action=make_action())
    outcome = reg.run_point(HookPoint.PRE_ACTION, ctx)

    assert calls == ["first", "second", "third"]
    assert outcome.decision == HookDecision.ALLOW


def test_hooks_for_returns_registered():
    reg = HookRegistry()
    h = RecordingHook("x", [], HookOutcome.allow())
    reg.register(h)
    assert reg.hooks_for(HookPoint.PRE_ACTION) == [h]
    assert reg.hooks_for(HookPoint.ON_AUDIT) == []


# --- DENY short-circuit + enforce -------------------------------------------

def test_deny_short_circuits_and_run_point_does_not_raise():
    calls = []
    reg = HookRegistry()
    reg.register(RecordingHook("a", calls, HookOutcome.allow()))
    reg.register(RecordingHook("b", calls, HookOutcome.deny("nope")))
    reg.register(RecordingHook("c", calls, HookOutcome.allow()))

    ctx = HookContext(point=HookPoint.PRE_ACTION, action=make_action())
    outcome = reg.run_point(HookPoint.PRE_ACTION, ctx)

    assert outcome.decision == HookDecision.DENY
    assert outcome.reason == "nope"
    assert calls == ["a", "b"]  # "c" never ran


def test_enforce_raises_hook_denied_error():
    reg = HookRegistry()
    reg.register(RecordingHook("denier", [], HookOutcome.deny("blocked")))
    ctx = HookContext(point=HookPoint.PRE_ACTION, action=make_action())

    with pytest.raises(HookDeniedError):
        reg.enforce(HookPoint.PRE_ACTION, ctx)


def test_enforce_allows_when_no_deny():
    reg = HookRegistry()
    reg.register(RecordingHook("ok", [], HookOutcome.allow()))
    ctx = HookContext(point=HookPoint.PRE_ACTION, action=make_action())
    outcome = reg.enforce(HookPoint.PRE_ACTION, ctx)
    assert outcome.decision == HookDecision.ALLOW


# --- MUTATE merges data ------------------------------------------------------

def test_mutate_merges_into_ctx_data_and_continues():
    calls = []
    reg = HookRegistry()
    reg.register(RecordingHook("m1", calls, HookOutcome.mutate({"added": 1})))
    reg.register(RecordingHook("m2", calls, HookOutcome.mutate({"more": 2})))

    ctx = HookContext(point=HookPoint.PRE_ACTION, action=make_action(), data={"orig": 0})
    outcome = reg.run_point(HookPoint.PRE_ACTION, ctx)

    assert outcome.decision == HookDecision.ALLOW
    assert calls == ["m1", "m2"]
    assert ctx.data == {"orig": 0, "added": 1, "more": 2}


# --- DestructiveGuard --------------------------------------------------------

def test_destructive_guard_denies_unconfirmed_delete_vm():
    guard = DestructiveGuard()
    ctx = HookContext(point=HookPoint.PRE_ACTION, action=make_action(name="delete_vm"))
    outcome = guard.run(ctx)
    assert outcome.decision == HookDecision.DENY


def test_destructive_guard_allows_confirmed_delete_vm():
    guard = DestructiveGuard()
    ctx = HookContext(
        point=HookPoint.PRE_ACTION,
        action=make_action(name="delete_vm", payload={"confirm": True}),
    )
    outcome = guard.run(ctx)
    assert outcome.decision == HookDecision.ALLOW


def test_destructive_guard_allows_approved_status():
    guard = DestructiveGuard()
    ctx = HookContext(
        point=HookPoint.PRE_ACTION,
        action=make_action(name="delete_snapshot", status="APPROVED"),
    )
    assert guard.run(ctx).decision == HookDecision.ALLOW


def test_destructive_guard_ignores_non_destructive_ops():
    guard = DestructiveGuard()
    ctx = HookContext(point=HookPoint.PRE_ACTION, action=make_action(name="list_vms"))
    assert guard.run(ctx).decision == HookDecision.ALLOW


def test_destructive_guard_custom_set():
    guard = DestructiveGuard(destructive_ops={"wipe_disk"})
    # default destructive op now allowed (not in custom set)
    ctx_default = HookContext(point=HookPoint.PRE_ACTION, action=make_action(name="delete_vm"))
    assert guard.run(ctx_default).decision == HookDecision.ALLOW
    # custom op denied
    ctx_custom = HookContext(point=HookPoint.PRE_ACTION, action=make_action(name="wipe_disk"))
    assert guard.run(ctx_custom).decision == HookDecision.DENY


# --- SecretScrub -------------------------------------------------------------

def test_secret_scrub_redacts_nested_and_keeps_non_secrets():
    data = {"token": "abc", "x": {"password": "p"}, "keep": "visible", "n": 5}
    ctx = HookContext(point=HookPoint.ON_AUDIT, data=data)
    outcome = SecretScrub().run(ctx)

    assert outcome.decision == HookDecision.MUTATE
    assert outcome.mutated_data == {
        "token": "<redacted>",
        "x": {"password": "<redacted>"},
        "keep": "visible",
        "n": 5,
    }
    # original untouched (scrub returns a copy)
    assert data["token"] == "abc"
    assert data["x"]["password"] == "p"


def test_scrub_helper_handles_lists_and_case_insensitive_keys():
    obj = {
        "items": [{"API_KEY": "k1"}, {"safe": "ok"}],
        "Authorization": "Bearer z",
        "private_key": "pem",
    }
    out = scrub(obj)
    assert out["items"][0]["API_KEY"] == "<redacted>"
    assert out["items"][1]["safe"] == "ok"
    assert out["Authorization"] == "<redacted>"
    assert out["private_key"] == "<redacted>"


def test_secret_scrub_via_registry_merges_redacted():
    reg = HookRegistry()
    reg.register(SecretScrub())
    ctx = HookContext(point=HookPoint.ON_AUDIT, data={"webhook_url": "https://x", "ok": 1})
    reg.run_point(HookPoint.ON_AUDIT, ctx)
    assert ctx.data == {"webhook_url": "<redacted>", "ok": 1}


# --- RateLimit ---------------------------------------------------------------

def test_rate_limit_denies_n_plus_one_within_window():
    clock = {"t": 1000.0}
    rl = RateLimit(max_calls=2, per_seconds=10.0, time_fn=lambda: clock["t"])
    ctx = HookContext(point=HookPoint.PRE_ACTION, action=make_action(name="reboot"))

    assert rl.run(ctx).decision == HookDecision.ALLOW   # 1
    assert rl.run(ctx).decision == HookDecision.ALLOW   # 2
    assert rl.run(ctx).decision == HookDecision.DENY    # 3 -> exceeded


def test_rate_limit_window_resets_after_time_passes():
    clock = {"t": 0.0}
    rl = RateLimit(max_calls=1, per_seconds=5.0, time_fn=lambda: clock["t"])
    ctx = HookContext(point=HookPoint.PRE_ACTION, action=make_action(name="reboot"))

    assert rl.run(ctx).decision == HookDecision.ALLOW
    assert rl.run(ctx).decision == HookDecision.DENY
    clock["t"] = 6.0  # outside the window
    assert rl.run(ctx).decision == HookDecision.ALLOW


def test_rate_limit_keys_by_action_name():
    clock = {"t": 0.0}
    rl = RateLimit(max_calls=1, per_seconds=10.0, time_fn=lambda: clock["t"])
    ctx_a = HookContext(point=HookPoint.PRE_ACTION, action=make_action(name="op_a"))
    ctx_b = HookContext(point=HookPoint.PRE_ACTION, action=make_action(name="op_b"))

    assert rl.run(ctx_a).decision == HookDecision.ALLOW
    assert rl.run(ctx_b).decision == HookDecision.ALLOW  # different key, own budget
    assert rl.run(ctx_a).decision == HookDecision.DENY


def test_rate_limit_rejects_bad_args():
    with pytest.raises(ValueError):
        RateLimit(max_calls=0, per_seconds=1.0)
    with pytest.raises(ValueError):
        RateLimit(max_calls=1, per_seconds=0)
