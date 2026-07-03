"""CNTC command-line entry point.

    cntc profiles                                   # list requirement profiles
    cntc verdict campaigns/<id>/results.json        # (re)grade an existing run -> scorecard
    cntc verdict <results.json> --profile performance --baseline <other results.json>
    cntc run --config configs/sdcore-bess.yaml      # run upfbench, then grade (delegates)

`cntc verdict` re-grades any past results.json without re-running anything — the whole point
of a data-driven verdict layer.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cntc import __version__
from cntc.standards import load_catalog, list_profiles, lint_catalog
from cntc.verdict import evaluate
from cntc.certification import render_console, write_scorecard


def _cmd_profiles(_args) -> int:
    print("CNTC requirement profiles:")
    for p in list_profiles():
        cat = load_catalog(p)
        ess = sum(1 for t in cat.get("tests", []) if t.get("class") == "essential")
        print(f"  {p:<14} {cat.get('title','')}  ({len(cat.get('tests',[]))} tests, {ess} essential)")
    return 0


def _cmd_lint(_args) -> int:
    """Structurally validate every requirement catalog (bad verdict kinds, ops, classes)."""
    bad = 0
    for p in list_profiles():
        issues = lint_catalog(load_catalog(p))
        if issues:
            bad += len(issues)
            print(f"✗ {p}: {len(issues)} issue(s)")
            for i in issues:
                print(f"    - {i}")
        else:
            print(f"✓ {p}: clean")
    return 1 if bad else 0


def _cmd_verdict(args) -> int:
    results_path = Path(args.results)
    if not results_path.exists():
        print(f"no such results.json: {results_path}", file=sys.stderr)
        return 2
    data = json.loads(results_path.read_text())
    catalog = load_catalog(args.profile)
    baseline = json.loads(Path(args.baseline).read_text()) if args.baseline else None

    rig = _rig_from_results(data)
    verdict = evaluate(data.get("suites", []), catalog, baseline=baseline, rig=rig)

    print(render_console(verdict))
    out = write_scorecard(results_path.parent, verdict)
    print(f"  scorecard: {out}")

    if args.write_back:
        data["verdict"] = verdict
        results_path.write_text(json.dumps(data, indent=2, default=str))
        print(f"  verdict written back into {results_path}")

    return 0 if verdict["result"] == "PASS" else 1


def _rig_from_results(data: dict) -> dict:
    sut = data.get("sut", {}) or {}
    return {
        "adapter": sut.get("adapter") or sut.get("upf") or "",
        "mode": sut.get("mode", ""),
        "rig_class": sut.get("rig_class", ""),
        "cpu": sut.get("cpu", ""),
        "nic": sut.get("nic", ""),
    }


def _cmd_run(args) -> int:
    # Delegate to the upfbench engine; it invokes the verdict layer itself at the end.
    from upfbench import runner
    runner.run(args.config, suite=args.suite, campaign=args.campaign, profile=args.profile)
    return 0


def _cmd_certify(args) -> int:
    """Issue a formal CNTC conformance certificate from a results.json — only if PASS."""
    from cntc.certification import certificate as C
    results_path = Path(args.results)
    if not results_path.exists():
        print(f"no such results.json: {results_path}", file=sys.stderr)
        return 2
    data = json.loads(results_path.read_text())
    verdict = data.get("verdict")
    if verdict is None:   # grade on the fly if the run wasn't graded yet
        catalog = load_catalog(args.profile)
        verdict = evaluate(data.get("suites", []), catalog, rig=_rig_from_results(data))
    cert = C.issue(verdict, data.get("sut", {}), data.get("started", ""))
    if cert is None:
        print(f"\n  ❌ NOT CERTIFIED\n  Reason: {C.refusal_reason(verdict)}")
        print("  A CNTC certificate is issued only when every essential test of the profile "
              "passes.\n")
        return 1
    print(C.render_markdown(cert))
    out_dir = results_path.parent
    (out_dir / "certificate.md").write_text(C.render_markdown(cert))
    (out_dir / "certificate.html").write_text(C.render_html(cert))
    (out_dir / "certificate.json").write_text(json.dumps(cert, indent=2))
    print(f"  certificate written: {out_dir}/certificate.{{md,html,json}}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cntc",
                                description="Cloud Native Telecom Certification Framework")
    p.add_argument("--version", action="version", version=f"CNTC {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("profiles", help="list requirement profiles").set_defaults(func=_cmd_profiles)
    sub.add_parser("lint", help="structurally validate the requirement catalogs").set_defaults(func=_cmd_lint)

    v = sub.add_parser("verdict", help="grade an existing results.json against a profile")
    v.add_argument("results", help="path to a campaign results.json")
    v.add_argument("--profile", default="conformance", help="requirement profile (default: conformance)")
    v.add_argument("--baseline", default=None, help="a prior results.json for relative grading")
    v.add_argument("--write-back", action="store_true", help="write the verdict into results.json")
    v.set_defaults(func=_cmd_verdict)

    r = sub.add_parser("run", help="run a campaign (delegates to the upfbench engine) then grade")
    r.add_argument("--config", required=True)
    r.add_argument("--suite", choices=["performance", "load", "pfcp", "n3neg",
                                       "conformance", "all"])
    r.add_argument("--campaign", default=None)
    r.add_argument("--profile", default="conformance")
    r.set_defaults(func=_cmd_run)

    cert = sub.add_parser("certify", help="issue a formal conformance certificate from a results.json (PASS only)")
    cert.add_argument("results", help="path to a campaign results.json")
    cert.add_argument("--profile", default="conformance", help="profile to grade against if not already graded")
    cert.set_defaults(func=_cmd_certify)

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
