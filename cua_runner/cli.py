"""Command-line entry points -- see README.md for the exact demo commands."""
from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

from cua_runner import schema as sc
from cua_runner.agent.loop import run_discovery
from cua_runner.agent.outcomes_catalog import LOOKUP_BALANCE_OUTCOMES, OPEN_SUBACCOUNT_OUTCOMES
from cua_runner.agent.recorder import build_capability
from cua_runner.guardrails.policy import DEFAULT_ALLOWLIST
from cua_runner.replay.engine import replay
from cua_runner.replay.stability import run_stability_test

load_dotenv()


def cmd_discover(args):
    result = run_discovery(goal=args.goal, base_url=args.base_url, allowlist=DEFAULT_ALLOWLIST,
                            headless=not args.headed, allow_escalation=args.allow_escalation)
    print(f"\n=== Discovery run finished: status={result.status} ===")
    print(result.final_message)
    print(f"Evidence saved to: {result.evidence_dir}")

    if result.status != "done":
        print("Run did not complete successfully -- nothing recorded.")
        sys.exit(1)

    target_app = sc.TargetAppProfile(
        vendor="mockbank", app_name="teller-console", base_url=args.base_url,
    )
    build_capability(
        result,
        capability_id=args.capability_id,
        name=args.name,
        target_app=target_app,
        allowlist=DEFAULT_ALLOWLIST,
        outcomes=(
            OPEN_SUBACCOUNT_OUTCOMES if args.capability_id == "open_savings_subaccount"
            else LOOKUP_BALANCE_OUTCOMES if args.capability_id == "lookup_member_balance"
            else []
        ),
    )


def cmd_replay(args):
    params = json.loads(args.params)
    result = replay(args.artifact, params, force_unattended=args.force_unattended,
                     headless=not args.headed, allow_escalation=args.allow_escalation)
    print(f"\n=== Replay result: {result.status.value} ===")
    print("message:", result.message)
    if result.outputs:
        print("outputs:", result.outputs)
    if result.business_result:
        print("business_result:", result.business_result)
    if result.debug:
        print("debug: step=%s expected=%s observed=%s" % (result.debug.step_id, result.debug.expected, result.debug.observed))
    if result.retry_guidance:
        print("retry_guidance:", result.retry_guidance)
    if result.drift_signals:
        print("drift_signals (fallback locator used):", result.drift_signals)
    if result.assisted_fallback_used:
        print("assisted_fallback_used:", result.assisted_fallback_used)


def cmd_stability(args):
    param_pool = json.loads(args.param_pool)
    report = run_stability_test(args.artifact, param_pool, args.n, force_unattended=args.force_unattended)
    print(f"\n=== Stability report: {report.n} runs ===")
    print(f"success: {report.success_count}")
    print(f"business outcomes: {report.business_outcome_counts}")
    print(f"hard failures (genuine flakiness signal): {report.hard_failure_count}")
    if report.hard_failure_details:
        for d in report.hard_failure_details:
            print("  -", d)
    print(f"runs with a drift signal (fallback locator used): {report.drift_signal_runs}")
    print(f"FLAKY: {report.flaky}")


def main():
    parser = argparse.ArgumentParser(prog="cua_runner")
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser("discover", help="Run an LLM-driven discovery run and record a capability.")
    p_discover.add_argument("--goal", required=True)
    p_discover.add_argument("--base-url", default="http://127.0.0.1:5000/")
    p_discover.add_argument("--capability-id", required=True)
    p_discover.add_argument("--name", required=True)
    p_discover.add_argument("--headed", action="store_true", help="Launch a visible browser window.")
    p_discover.add_argument("--allow-escalation", action="store_true",
                             help="Enable human handoff if the agent gets stuck (requires --headed to be meaningful).")
    p_discover.set_defaults(func=cmd_discover)

    p_replay = sub.add_parser("replay", help="Deterministically replay a saved capability artifact.")
    p_replay.add_argument("--artifact", required=True)
    p_replay.add_argument("--params", required=True, help="JSON object of input parameters.")
    p_replay.add_argument("--force-unattended", action="store_true",
                           help="Bypass the draft-approval gate for irreversible capabilities (demo/testing only).")
    p_replay.add_argument("--headed", action="store_true", help="Launch a visible browser window.")
    p_replay.add_argument("--allow-escalation", action="store_true",
                           help="Enable human handoff on a hard failure (requires --headed to be meaningful).")
    p_replay.set_defaults(func=cmd_replay)

    p_stability = sub.add_parser("stability", help="Replay a capability N times and report a flakiness signal.")
    p_stability.add_argument("--artifact", required=True)
    p_stability.add_argument("--param-pool", required=True, help="JSON array of param objects to cycle through.")
    p_stability.add_argument("--n", type=int, default=5)
    p_stability.add_argument("--force-unattended", action="store_true")
    p_stability.set_defaults(func=cmd_stability)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
