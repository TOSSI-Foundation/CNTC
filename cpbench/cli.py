"""cpbench command-line entry point, the CNTC control-plane test engine.

    cpbench list                                    # NFs + their catalogs/test counts
    cpbench doctor --config configs/free5gc-cp.yaml # preflight the rig
    cpbench run  --config configs/free5gc-cp.yaml --nf amf     # test one NF -> scorecard
    cpbench run  --config configs/free5gc-cp.yaml --nf all     # test every NF -> composite

Emits the same results.json schema as upfbench, so `cntc verdict` / `cntc certify` and the
dashboard work on control-plane campaigns unchanged.
"""
from __future__ import annotations

import argparse
import sys

from cpbench import __version__, config as cfgmod


def _cmd_list(_args) -> int:
    from cntc.standards import load_catalog
    from cpbench.suites.registry import NF_REQUIRES
    print("cpbench, control-plane NFs and their CNTC catalogs:")
    for nf in cfgmod.NFS:
        try:
            cat = load_catalog(cfgmod.Campaign.profile_for(nf))
        except FileNotFoundError:
            print(f"  {nf:<6} (no catalog)")
            continue
        ess = sum(1 for t in cat.get("tests", []) if t.get("class") == "essential")
        req = NF_REQUIRES.get(nf, {})
        drv = "+".join(x for x in (req.get("gnb"), "sbi" if req.get("sbi") else None) if x)
        print(f"  {nf:<6} {cat.get('title',''):<52} "
              f"{len(cat.get('tests',[])):>2} tests, {ess} essential   [{drv}]")
    return 0


def _cmd_doctor(args) -> int:
    from cpbench import doctor
    return doctor.run(args.config)


def _cmd_run(args) -> int:
    from cpbench import runner
    # Defensive terminal-restore: the run spawns external tools (UERANSIM/tcpdump). They are
    # already detached from the TTY (start_new_session), but as belt-and-suspenders we snapshot
    # the terminal mode and restore it afterwards, so a clean prompt is guaranteed on camera.
    saved = None
    try:
        import termios
        if sys.stdout.isatty():
            saved = termios.tcgetattr(sys.stdout.fileno())
    except Exception:  # noqa: BLE001, not a tty / no termios: nothing to restore
        saved = None
    try:
        runner.run(args.config, nf=args.nf, campaign=args.campaign)
    finally:
        if saved is not None:
            try:
                termios.tcsetattr(sys.stdout.fileno(), termios.TCSANOW, saved)
            except Exception:  # noqa: BLE001
                pass
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cpbench",
                                description="CNTC control-plane test engine (AMF/SMF/NRF/AUSF/UDM)")
    p.add_argument("--version", action="version", version=f"cpbench {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="list NFs + catalogs").set_defaults(func=_cmd_list)

    d = sub.add_parser("doctor", help="preflight the rig for a control-plane run")
    d.add_argument("--config", required=True)
    d.set_defaults(func=_cmd_doctor)

    r = sub.add_parser("run", help="run a control-plane campaign then grade")
    r.add_argument("--config", required=True)
    r.add_argument("--nf", choices=list(cfgmod.VALID_TARGETS), default=None,
                   help="which NF to test (default: the config's target_nf)")
    r.add_argument("--campaign", default=None)
    r.set_defaults(func=_cmd_run)

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
