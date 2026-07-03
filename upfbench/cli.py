"""upfbench CLI — the three-choice menu + non-interactive runner.

Usage:
    upfbench                                  # interactive: pick a suite, point at a config
    upfbench run --config configs/sdcore-bess.yaml
    upfbench run --config ... --suite performance   # override the config's suite
    upfbench list                             # show suites and their test cases
"""
from __future__ import annotations

import argparse
import sys

from upfbench.suites.registry import build_suite

SUITES = {
    "1": ("performance", "UPF Performance", "throughput · latency · jitter · burst"),
    "2": ("load",        "Multi-UE Load",  "N UEs · capacity · per-UE rate · p99"),
    "3": ("pfcp",        "PFCP Conformance","N4 association + session correctness"),
    "4": ("n3neg",       "N3 Robustness",  "malformed GTP-U · unknown TEID · crash/recovery"),
}


def _menu() -> str:
    print("\n  Which test do you want to run?\n")
    for k, (_, title, blurb) in SUITES.items():
        print(f"   {k}) {title:<18} {blurb}")
    print("   5) conformance        pfcp + n3neg  → CNTC conformance verdict")
    print("   6) all                performance + load + pfcp\n")
    choice = input("  pick [1-6]: ").strip()
    return {"1": "performance", "2": "load", "3": "pfcp", "4": "n3neg",
            "5": "conformance", "6": "all"}.get(choice, "")


def _cmd_list(_args) -> int:
    for suite in ("performance", "load", "pfcp", "n3neg"):
        print(f"\n{suite}:")
        for case in build_suite(suite):
            print(f"  {case.id:<6} {case.name}")
    return 0


def _cmd_run(args) -> int:
    from upfbench import runner, config as cfgmod
    if args.suite:
        # validate early; the override is applied by re-reading + mutating the campaign
        if args.suite not in cfgmod.VALID_SUITES:
            print(f"invalid --suite {args.suite!r}", file=sys.stderr)
            return 2
    runner.run(args.config, suite=args.suite,
               reset_between_suites=args.reset_between_suites,
               campaign=args.campaign, profile=args.profile)
    return 0


def _cmd_dashboard(args) -> int:
    """Launch the results dashboard (web UI over campaigns/)."""
    import os
    from pathlib import Path
    # the dashboard package lives at the repo root, beside upfbench/
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from dashboard.app import app
    except ImportError as e:
        print(f"dashboard needs dash + plotly. Install them: pip install --user dash plotly\n"
              f"  (or run scripts/bootstrap_fresh_vm.sh). Import error: {e}", file=sys.stderr)
        return 1
    host = args.host or os.environ.get("UPFBENCH_DASH_HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("UPFBENCH_DASH_PORT", "8050"))
    print(f"upfbench dashboard → http://{host}:{port}  "
          f"(reads campaigns/ live; refresh to see new runs; Ctrl-C to stop)")
    app.run(host=host, port=port, debug=False)
    return 0


def _cmd_doctor(_args) -> int:
    from upfbench.doctor import run as doctor_run
    return doctor_run()


def _cmd_interactive(_args) -> int:
    suite = _menu()
    if not suite:
        print("no suite selected", file=sys.stderr)
        return 2
    config = input("  path to campaign config: ").strip()
    if not config:
        print("a config is required (see configs/sdcore-bess.yaml)", file=sys.stderr)
        return 2
    from upfbench import runner
    runner.run(config)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="upfbench", description=__doc__)
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="run a campaign from a config")
    r.add_argument("--config", required=True)
    r.add_argument("--suite", choices=["performance", "load", "pfcp", "n3neg",
                                       "conformance", "all"])
    r.add_argument("--reset-between-suites", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="reset the UPF to a clean state before each suite "
                        "(--reset-between-suites / --no-reset-between-suites); "
                        "overrides the config's reset_between_suites")
    r.add_argument("--campaign", default=None,
                   help="override the config's campaign id (e.g. UPF-BM-SDCORE-DPDK); "
                        "lets one config serve multiple modes with separate output dirs")
    r.add_argument("--profile", default=None,
                   help="CNTC requirement profile to grade against (conformance | performance); "
                        "overrides the config's profile. The run ends with a graded scorecard.")
    r.set_defaults(func=_cmd_run)

    sub.add_parser("list", help="list suites and test cases").set_defaults(func=_cmd_list)

    d = sub.add_parser("dashboard", help="launch the results dashboard (web UI)")
    d.add_argument("--host", default=None, help="bind address (default 0.0.0.0)")
    d.add_argument("--port", type=int, default=None, help="port (default 8050)")
    d.set_defaults(func=_cmd_dashboard)

    sub.add_parser("doctor", help="check this machine is ready to run upfbench"
                   ).set_defaults(func=_cmd_doctor)

    args = p.parse_args(argv)
    if not args.cmd:
        return _cmd_interactive(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
