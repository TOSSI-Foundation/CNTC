"""Orchestrator: wire the plugins, run the chosen suite(s), store + report.

This is the 5-step loop from the plan, made concrete:
  1. load the UPF adapter           (adapters/<name>.py)
  2. for each suite -> wire control + traffic per registry.SUITE_REQUIRES
  3. run each TestCase, collect TestResult
  4. store results.json (+ captured commands)
  5. render the report

Phase 0 runs end-to-end with stub tests (they return status="error"/"not implemented"),
proving the pipeline and producing a report skeleton.
"""
from __future__ import annotations

from pathlib import Path

from upfbench import config as cfgmod
from upfbench.adapters.base import load_adapter
from upfbench.control.base import load_control
from upfbench.traffic.base import load_generator
from upfbench.results import Store, SuiteResult
from upfbench.report import render
from upfbench.suites.base import RunContext
from upfbench.suites.registry import build_suite, SUITE_REQUIRES


def run(config_path: str, campaigns_root: str = "campaigns", suite: str | None = None,
        reset_between_suites: bool | None = None, campaign: str | None = None,
        profile: str | None = None) -> Path:
    cfg = cfgmod.load(config_path)
    # CLI --profile overrides the config's profile; else use the config's (default 'conformance').
    profile = profile if profile is not None else getattr(cfg, "profile", "conformance")
    if suite:                      # CLI --suite overrides the config's suite
        cfg.suite = suite
    if reset_between_suites is not None:   # CLI --[no-]reset-between-suites overrides config
        cfg.reset_between_suites = reset_between_suites
    if campaign:                   # CLI --campaign overrides the config's campaign id
        cfg.campaign = campaign
    store = Store(Path(campaigns_root), cfg.campaign)

    upf = load_adapter(cfg.upf.adapter, cfg.upf, store)
    upf.deploy()
    # Read live SUT facts (mode, image, ports). Non-fatal: a failure here means the UPF
    # is unreachable, but we still want a report that records that rather than crashing.
    live_mode = ""
    try:
        facts = upf.describe()
        store.set_sut_live(facts)
        live_mode = str(facts.get("mode", "")).lower()
    except Exception as e:  # noqa: BLE001 - surface as a report note, don't abort
        print(f"[upfbench] warning: could not read SUT facts from UPF: {e}")
        store.set_sut_live({"sut_probe_error": str(e)})
    # LIVE: write an initial "running" checkpoint so the campaign appears on the dashboard
    # the moment it starts, then re-checkpoint per suite (below) so tests show up as they run.
    store.save(sut=cfg.sut, status="running")
    try:
        for suite_name in cfg.suites:
            # Start each suite from a clean UPF when asked: one suite's session churn /
            # saturating traffic can degrade the datapath for the next (see adapter.reset).
            if cfg.reset_between_suites:
                print(f"[upfbench] resetting UPF for a clean {suite_name} run ...")
                try:
                    upf.reset()
                except Exception as e:  # noqa: BLE001 — a failed reset shouldn't abort the run
                    print(f"[upfbench] warning: UPF reset failed ({e}); running on current state")
            print(f"[upfbench] running suite: {suite_name}")
            store.save(sut=cfg.sut, status="running", running_suite=suite_name)  # LIVE
            req = SUITE_REQUIRES.get(suite_name, {})
            knobs = getattr(cfg, suite_name, {})
            # The config's per-suite block can override the default plugins
            # (e.g. performance.generator: tcpreplay, performance.control: pybess).
            control_name = knobs.get("control") or req.get("control")
            traffic_name = knobs.get("generator") or req.get("traffic")
            if traffic_name:
                _guard_injection(live_mode, traffic_name)
            control = _maybe(load_control, control_name, cfg.upf, store)
            traffic = _maybe(load_generator, traffic_name, cfg.upf, store)

            ctx = RunContext(cfg=cfg, upf=upf, control=control, traffic=traffic,
                             store=store, knobs=knobs)
            if control is not None:
                control.setup()
            sres = SuiteResult(suite=suite_name)
            try:
                for case in build_suite(suite_name):
                    print(f"  - {case.id} {case.name}")
                    try:
                        sres.tests.append(case.run(ctx))
                    except Exception as e:  # noqa: BLE001 — one bad test must not kill the suite
                        from upfbench.results import TestResult
                        msg = f"{type(e).__name__}: {e}"
                        print(f"    ! {case.id} errored: {msg}")
                        sres.tests.append(
                            TestResult(case.id, case.name, "error", notes=msg))
            finally:
                if control is not None:
                    try:
                        control.teardown()
                    except Exception as e:  # noqa: BLE001
                        print(f"[upfbench] warning: control teardown failed: {e}")
            store.add_suite(sres)
            store.save(sut=cfg.sut, status="running")   # LIVE: this suite's results now visible
    finally:
        upf.teardown()

    # CNTC verdict layer (optional): grade what ran against a requirement profile and
    # emit a scorecard. No-op if the `cntc` package isn't importable, so the engine still
    # runs standalone.
    _apply_cntc_verdict(store, cfg, profile, live_mode)

    results_path = store.save(sut=cfg.sut)
    print(f"[upfbench] results: {results_path}")
    reports = render.build(results_path, store.dir, cfg)
    for r in reports:
        print(f"[upfbench] report:  {r}")
    return reports[0] if reports else results_path


def _maybe(loader, name, cfg, store):
    return loader(name, cfg, store) if name else None


def _apply_cntc_verdict(store, cfg, profile: str | None, live_mode: str) -> None:
    """Grade the collected results against a CNTC requirement profile and attach the
    verdict + write a scorecard. Silent no-op if `cntc` isn't installed, the profile is
    unknown, or none of that profile's tests actually ran (so the profile doesn't apply)."""
    if not profile:
        return
    try:
        from cntc.standards import load_catalog
        from cntc.verdict import evaluate
        from cntc.certification import render_console, write_scorecard
    except ImportError:
        return  # engine runs standalone without the umbrella layer
    try:
        catalog = load_catalog(profile)
    except FileNotFoundError as e:
        print(f"[cntc] {e}")
        return

    baseline = None
    if getattr(cfg, "baseline", None):
        try:
            from upfbench.results import Store
            baseline = Store.load(cfg.baseline)
        except Exception as e:  # noqa: BLE001
            print(f"[cntc] warning: could not load baseline {cfg.baseline!r}: {e}")

    sut = cfg.sut or {}
    rig = {
        "adapter": cfg.upf.adapter,
        "mode": live_mode or cfg.upf.mode or sut.get("mode", ""),
        "rig_class": sut.get("rig_class", ""),
        "cpu": sut.get("cpu", ""),
        "nic": sut.get("nic", ""),
    }
    verdict = evaluate(store.suite_dicts(), catalog, baseline=baseline, rig=rig)

    judged = sum(1 for t in verdict.get("tests", []) if t["outcome"] in ("pass", "fail"))
    if judged == 0:
        # None of this profile's tests ran in this campaign — don't emit a misleading verdict.
        print(f"[cntc] profile {profile!r}: no applicable tests ran this campaign; "
              f"skipping verdict. (Run the pfcp / n3neg suites for a conformance verdict.)")
        return

    store.set_verdict(verdict)
    print(render_console(verdict))
    path = write_scorecard(store.dir, verdict)
    print(f"[cntc] scorecard: {path}")


# Which UPF dataplane modes each generator can actually inject into. tcpreplay needs a
# host kernel socket (af_packet); trex/testpmd hairpin GTP-U through a NIC VF into the
# UPF's DPDK/XDP-owned access VF (af_xdp/dpdk/cndp). Mixing them silently measures the
# generator, not the UPF — so we fail fast with an explanation. (Empty/unknown live mode
# skips the guard rather than blocking.)
_INJECTION_OK = {
    # OAI-UPF's "simpleswitch" datapath is a userspace switch on a normal container netdev
    # (reachable via the host docker bridge + ARP) — i.e. host af_packet injection, which
    # tcpreplay drives. It is NOT a DPDK/XDP-owned VF, so allow it here.
    # "gtp5g" (Open5GS / free5GC) is the in-kernel GTP-U module bound to a normal netdev
    # (eth0 on a docker bridge), so tcpreplay injects into it the same way — verified live
    # on free5GC (a TEID-aligned GTP-U blast decapsulated on upfgtp). Not a DPDK/XDP VF.
    "tcpreplay": {"af_packet", "linux", "simpleswitch", "gtp5g"},
    "trex": {"af_xdp", "dpdk", "cndp"},
    "testpmd": {"af_xdp", "dpdk", "cndp"},
}


def _guard_injection(live_mode: str, traffic_name: str) -> None:
    ok = _INJECTION_OK.get(traffic_name)
    if not ok or not live_mode or live_mode in ok:
        return
    raise RuntimeError(
        f"generator '{traffic_name}' cannot drive a UPF in '{live_mode}' mode "
        f"(it supports: {sorted(ok)}). The '{live_mode}' access port is a "
        f"{'DPDK/XDP-owned VF (use trex)' if traffic_name == 'tcpreplay' else 'host kernel socket (use tcpreplay)'}. "
        f"Use the matching config (sdcore-bess.yaml=af_packet/tcpreplay, "
        f"sdcore-bess-trex.yaml=dpdk|af_xdp|cndp/trex).")

