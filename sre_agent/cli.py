"""CLI entry point for the SRE Agent.

Thin argument parsing over :class:`sre_agent.coordinator.Coordinator` — the
sole CLI (the legacy ``sre_agent.main`` monolith was removed in the cleanup
pass). Exit codes: 0 ok, 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

from .coordinator import Coordinator
from .core.models import Action

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_HELP_VMID, _HELP_NODE, _HELP_TYPE = "VM ID", "Node name", "VM type"


def _parse_payload(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid --payload JSON: {e}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(parsed, dict):
        print("--payload must be a JSON object", file=sys.stderr)
        raise SystemExit(2)
    return parsed


def _print_result(r) -> int:
    if r.success:
        print(json.dumps(r.result, indent=2, default=str))
        return 0
    print(f"Operation not completed: {r.error}", file=sys.stderr)
    return 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SRE Agent CLI")
    p.add_argument("--config", "-c", default=None, help="Path to config file")
    p.add_argument("--profile", "-p", default=None, help="Environment profile")
    p.add_argument("--debug", action="store_true", help="Enable debug logging")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("discover", help="Discover inventory")
    sub.add_parser("list-nodes", help="List Proxmox nodes")
    sub.add_parser("list-vms", help="List all VMs")
    sub.add_parser("health", help="Show connector health")
    sub.add_parser("graph", help="Show the resource graph (data plane)")
    dr = sub.add_parser("drift", help="Detect drift vs desired state")
    dr.add_argument("--closed-world", action="store_true",
                    help="Also flag resources present but not desired")
    rem = sub.add_parser("remediate", help="Propose change-sets to remediate drift")
    rem.add_argument("--closed-world", action="store_true")
    sub.add_parser("metrics", help="Render metrics in Prometheus text format")

    sk = sub.add_parser("skill", help="Run a skill (workflow) — submits a batch through the gate")
    sk.add_argument("--name", required=True, help="Skill name (e.g. rolling_restart)")
    sk.add_argument("--params", help="JSON params for the skill")
    sk.add_argument("--auto-approve", action="store_true")

    st = sub.add_parser("status", help="Get VM status")
    st.add_argument("--vmid", required=True, help=_HELP_VMID)
    st.add_argument("--node", required=True, help=_HELP_NODE)
    st.add_argument("--type", choices=["qemu", "lxc"], help=_HELP_TYPE)

    mon = sub.add_parser("monitor", help="Run health/workload checks")
    mon.add_argument("--output", "-o", choices=["table", "json"], default="table")

    run = sub.add_parser("run", help="Run a single operation")
    run.add_argument("--operation", required=True)
    run.add_argument("--target", default="")
    run.add_argument("--payload", help="JSON payload")
    run.add_argument("--emergency", action="store_true",
                     help="Emergency mode (downgrades only allow-listed ops)")

    bp = sub.add_parser("batch", help="Manage approval batches")
    bsub = bp.add_subparsers(dest="batch_command")
    bc = bsub.add_parser("create", help="Create a batch")
    bc.add_argument("--operation", required=True)
    bc.add_argument("--target", default="")
    bc.add_argument("--payload", help="JSON payload")
    bc.add_argument("--batch-id")
    bc.add_argument("--window", type=int, default=180)
    bc.add_argument("--auto-approve", action="store_true")
    bsub.add_parser("list", help="List batch actions")
    ba = bsub.add_parser("approve", help="Approve action(s)")
    ba.add_argument("--action-id", required=True)
    ba.add_argument("--approver", required=True, help="Approver identity (recorded)")
    bd = bsub.add_parser("decline", help="Decline action(s)")
    bd.add_argument("--action-id", required=True)

    au = sub.add_parser("audit", help="Show audit log")
    au.add_argument("--export", help="Export to file")

    em = sub.add_parser("emergency", help="Execute an emergency action")
    em.add_argument("--operation", required=True)
    em.add_argument("--target", default="")
    em.add_argument("--payload", help="JSON payload")
    return p


def _dispatch(args, c: Coordinator) -> int:
    cmd = args.command
    if cmd == "discover":
        print(json.dumps(c.discover(), indent=2, default=str))
        return 0
    if cmd == "health":
        print(json.dumps(c.health(), indent=2, default=str))
        return 0
    if cmd == "graph":
        print(json.dumps([vars(r) for r in c.resource_graph().all()], indent=2, default=str))
        return 0
    if cmd == "drift":
        findings = c.detect_drift(closed_world=args.closed_world)
        print(json.dumps([{"kind": f.kind.value, "resource": f.resource_key,
                           "detail": f.detail, "severity": f.severity} for f in findings],
                         indent=2))
        return 0
    if cmd == "remediate":
        out = c.remediate_drift(closed_world=args.closed_world)
        print(json.dumps([vars(x) for x in out], indent=2, default=str))
        return 0
    if cmd == "metrics":
        print(c.metrics.render_prometheus())
        return 0
    if cmd == "skill":
        batch = c.run_skill(args.name, _parse_payload(args.params),
                            auto_approve=args.auto_approve)
        print(f"Skill '{args.name}' planned {len(batch.actions)} action(s) as batch {batch.batch_id}")
        for a in batch.actions:
            print(f"  {a.id}: {a.name} -> {a.target} [{a.status}]")
        if not args.auto_approve:
            ok = c.trigger_approvals(batch)
            print("Approval request sent." if ok
                  else "WARNING: no approval channel succeeded — batch will NOT run (fail-closed).",
                  file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
        return 0
    if cmd == "list-nodes":
        return _print_result(c.execute("list_nodes"))
    if cmd == "list-vms":
        return _print_result(c.execute("list_vms"))
    if cmd == "status":
        return _print_result(c.execute("get_vm_status", target=f"{args.node}:{args.vmid}",
                                       payload={"node": args.node, "vmid": args.vmid,
                                                "type": args.type or "qemu"}))
    if cmd == "monitor":
        print(c.monitor(output=args.output))
        return 0
    if cmd == "run":
        return _print_result(c.execute(args.operation, target=args.target,
                                       payload=_parse_payload(args.payload),
                                       emergency=args.emergency))
    if cmd == "emergency":
        print("EMERGENCY MODE - approval bypassed only for allow-listed ops; logged.",
              file=sys.stderr)
        return _print_result(c.execute(args.operation, target=args.target,
                                       payload=_parse_payload(args.payload), emergency=True))
    if cmd == "audit":
        if args.export:
            c.audit.export(args.export)
            print(f"Audit log exported to: {args.export}")
        else:
            print(json.dumps(c.audit_log(), indent=2, default=str))
        return 0
    if cmd == "batch":
        return _dispatch_batch(args, c)
    return 2


def _dispatch_batch(args, c: Coordinator) -> int:
    bc = args.batch_command
    if bc == "create":
        action = Action(id=f"action_{args.operation}", name=args.operation,
                        target=args.target or "unknown", payload=_parse_payload(args.payload))
        batch = c.submit_batch([action], batch_id=args.batch_id, window_minutes=args.window,
                               auto_approve=args.auto_approve)
        print(f"Batch created: {batch.batch_id} ({len(batch.actions)} actions)")
        if not args.auto_approve:
            ok = c.trigger_approvals(batch)
            print("Approval request sent." if ok
                  else "WARNING: no approval channel succeeded — batch will NOT run (fail-closed).",
                  file=sys.stderr if not ok else sys.stdout)
            return 0 if ok else 1
        return 0
    if bc == "list":
        if not c.batch:
            print("No active batch")
            return 0
        for a in c.batch.actions:
            print(f"  {a.id}: {a.name} -> {a.target} [{a.status}]")
        return 0
    if bc == "approve":
        results = c.approve(args.action_id, approver=args.approver)
        if not results:
            print("Nothing approved (no active batch, no match, or window expired).",
                  file=sys.stderr)
            return 1
        print(f"Executed {len(results)} action(s); "
              f"{sum(1 for r in results if r.success)} succeeded.")
        return 0
    if bc == "decline":
        if not c.batch:
            print("No active batch", file=sys.stderr)
            return 1
        for a in c.batch.actions:
            if args.action_id in ("all", a.id):
                a.status = "DECLINED"
        print("Declined.")
        return 0
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    if not args.command:
        parser.print_help()
        return 0
    try:
        c = Coordinator(profile=args.profile, config_path=args.config)
    except Exception as e:  # noqa: BLE001 - top-level CLI guard
        logger.error("Failed to initialize coordinator: %s", e)
        return 1
    try:
        return _dispatch(args, c)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - top-level CLI guard
        logger.error("Command '%s' failed: %s", args.command, e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
