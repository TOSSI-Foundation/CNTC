"""ranbench command-line entry point, the CNTC RAN test engine.

    ranbench list                                     # product classes + catalogs/test counts
    ranbench doctor --config configs/ocudu-ran.yaml   # preflight the rig
    ranbench run  --config configs/ocudu-ran.yaml --target cucp   # one class -> scorecard
    ranbench run  --config configs/ocudu-ran.yaml --target all    # whole gNB -> composite

Emits the same results.json schema as upfbench/cpbench, so `cntc verdict` / `cntc certify` and
the dashboard work on RAN campaigns unchanged.
"""
from __future__ import annotations

import argparse
import sys

from ranbench import __version__, config as cfgmod

_LABELS = {"cucp": "O-CU-CP", "cuup": "O-CU-UP", "du": "O-DU",
           "pnf": "PNF", "vnf": "VNF"}


def _cmd_list(_args) -> int:
    from cntc.standards import load_catalog
    from ranbench.suites.registry import build_suite
    from ranbench.suites.base import StubCase
    print("ranbench, product classes and their CNTC catalogs:")
    print("  -- CU/DU split (3GPP TS 33.523) --")
    for tgt in cfgmod.CUDU_TARGETS + ("|fapi|",) + cfgmod.FAPI_TARGETS:
        if tgt == "|fapi|":
            print("  -- L1/L2 split over nFAPI (SCF222 / SCF225, not a 3GPP SCAS class) --")
            continue
        try:
            cat = load_catalog(cfgmod.Campaign.profile_for(tgt))
        except FileNotFoundError:
            print(f"  {tgt:<5} (no catalog)")
            continue
        tests = cat.get("tests", [])
        ess = sum(1 for t in tests if t.get("class") == "essential")
        impl = sum(1 for c in build_suite(tgt) if not isinstance(c, StubCase))
        print(f"  {tgt:<5} {_LABELS.get(tgt, ''):<9} {cat.get('title','')[:46]:<46} "
              f"{len(tests):>2} tests, {ess} essential, {impl} implemented")
    return 0


def _cmd_doctor(args) -> int:
    from ranbench import doctor
    return doctor.run(args.config)


def _cmd_run(args) -> int:
    from ranbench import runner
    # Defensive terminal-restore: the run spawns external tools (the RAN apps, a UE, tcpdump).
    # They are detached from the TTY, but we snapshot and restore the terminal mode anyway so a
    # clean prompt is guaranteed.
    saved = None
    try:
        import termios
        if sys.stdout.isatty():
            saved = termios.tcgetattr(sys.stdout.fileno())
    except Exception:  # noqa: BLE001, not a tty / no termios: nothing to restore
        saved = None
    try:
        runner.run(args.config, target=args.target, campaign=args.campaign)
    except KeyboardInterrupt:
        # the runner already tore the RAN down; report the standard SIGINT status
        print("[ranbench] stopped by user")
        return 130
    finally:
        if saved is not None:
            try:
                termios.tcsetattr(sys.stdout.fileno(), termios.TCSANOW, saved)
            except Exception:  # noqa: BLE001
                pass
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="ranbench",
        description="CNTC RAN test engine (split gNB: O-CU-CP / O-CU-UP / O-DU)")
    p.add_argument("--version", action="version", version=f"ranbench {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="list product classes + catalogs").set_defaults(func=_cmd_list)

    d = sub.add_parser("doctor", help="preflight the rig for a RAN run")
    d.add_argument("--config", required=True)
    d.set_defaults(func=_cmd_doctor)

    r = sub.add_parser("run", help="run a RAN campaign then grade")
    r.add_argument("--config", required=True)
    r.add_argument("--target", choices=list(cfgmod.VALID_TARGETS), default=None,
                   help="which product class to test (default: the config's target)")
    r.add_argument("--campaign", default=None)
    r.set_defaults(func=_cmd_run)

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
