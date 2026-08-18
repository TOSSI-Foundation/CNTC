"""Orchestrator: wire the plugins, run the chosen product-class suite(s), store + grade.

The RAN analog of ``cpbench.runner``:
  1. load the RAN adapter                    (adapters/<name>.py)
  2. read live SUT facts from the stack under test
  3. for each target -> wire the UE driver / core peer / observer per registry.TARGET_REQUIRES
  4. run each RanTestCase, collect TestResult -> SuiteResult(suite=<target>)
  5. store results.json; grade each target against its <target>-conformance catalog; write
     scorecards + a composite gNB verdict

Until the per-class suites land (P2-P4) every case is a StubCase -> 'not_implemented' -> graded
'na' -> INCOMPLETE. That is the pipeline working correctly, not a passing RAN.
"""
from __future__ import annotations

from pathlib import Path

from cntc_common.results import Store, SuiteResult, TestResult
from ranbench import config as cfgmod
from ranbench.adapters.base import load_adapter
from ranbench.drivers.base import load_driver
from ranbench.suites.base import RunContext
from ranbench.suites.registry import build_suite, TARGET_REQUIRES


def run(config_path: str, campaigns_root: str = "campaigns", target: str | None = None,
        campaign: str | None = None) -> Path:
    cfg = cfgmod.load(config_path)
    if target:                      # CLI --target overrides the config
        if target not in cfgmod.VALID_TARGETS:
            raise ValueError(f"--target must be one of {cfgmod.VALID_TARGETS}")
        cfg.target = target
    if campaign:
        cfg.campaign = campaign
    store = Store(Path(campaigns_root), cfg.campaign)

    ran = load_adapter(cfg.ran.adapter, cfg, store)
    ran.deploy()
    live_facts: dict = {}
    try:
        live_facts = ran.describe()
        store.set_sut_live(live_facts)
    except Exception as e:  # noqa: BLE001, surface as a note, don't abort
        print(f"[ranbench] warning: could not read RAN facts: {e}")
        store.set_sut_live({"ran_probe_error": str(e)})

    core = _maybe_core(cfg, store)
    if core is not None and cfg.subscribers:
        try:
            prov = core.provision_subscribers(cfg.subscribers)
            print(f"[ranbench] subscribers: {prov}")
        except Exception as e:  # noqa: BLE001
            print(f"[ranbench] warning: subscriber provisioning failed: {e}")

    store.save(sut=cfg.sut, status="running")   # LIVE: appear on the dashboard immediately
    try:
        for tgt in cfg.targets:
            print(f"[ranbench] testing target: {tgt}")
            store.save(sut=cfg.sut, status="running", running_suite=tgt)
            req = TARGET_REQUIRES.get(tgt, {})
            ue = _maybe_driver(cfg.drivers.get("ue"), cfg, store) if req.get("ue") else None
            observer = _maybe_observer(cfg, store) if req.get("observer") else None
            endpoint = ""
            try:
                endpoint = ran.node_endpoint(tgt)
            except Exception as e:  # noqa: BLE001
                print(f"[ranbench] warning: could not resolve {tgt} endpoint: {e}")

            if ue is not None:
                try:
                    ue.setup()
                except Exception as e:  # noqa: BLE001
                    print(f"[ranbench] warning: driver {ue.name} setup failed: {e}")

            ctx = RunContext(cfg=cfg, ran=ran, core=core, ue=ue, observer=observer,
                             store=store, target=tgt, endpoint=endpoint,
                             knobs=cfg.knobs.get(tgt, {}))
            sres = SuiteResult(suite=tgt)
            try:
                for case in build_suite(tgt):
                    print(f"  - {case.id} {case.name}")
                    try:
                        sres.tests.append(case.run(ctx))
                    except Exception as e:  # noqa: BLE001, one bad test must not kill the suite
                        msg = f"{type(e).__name__}: {e}"
                        print(f"    ! {case.id} errored: {msg}")
                        sres.tests.append(TestResult(case.id, case.name, "error", notes=msg))
            finally:
                if ue is not None:
                    try:
                        ue.teardown()
                    except Exception:  # noqa: BLE001
                        pass
            store.add_suite(sres)
            store.save(sut=cfg.sut, status="running")
    finally:
        ran.teardown()

    _apply_verdicts(store, cfg, live_facts)
    results_path = store.save(sut=cfg.sut)
    print(f"[ranbench] results: {results_path}")
    return results_path


def _maybe_driver(name, cfg, store):
    if not name:
        return None
    try:
        return load_driver(name, cfg, store)
    except Exception as e:  # noqa: BLE001
        print(f"[ranbench] warning: driver {name!r} unavailable: {e}")
        return None


def _maybe_core(cfg, store):
    """The 5G core peer is optional plumbing, not the subject: if it can't be loaded the run
    still proceeds and the tests that need it grade 'na'."""
    if not cfg.core.adapter:
        return None
    try:
        from ranbench.drivers.base import load_driver as _ld
        return _ld(f"core_{cfg.core.adapter}", cfg, store)
    except Exception as e:  # noqa: BLE001
        print(f"[ranbench] warning: core peer {cfg.core.adapter!r} unavailable: {e}")
        return None


def _maybe_observer(cfg, store):
    try:
        from ranbench.observers.wire import WireObserver
        return WireObserver(cfg, store)
    except Exception:  # noqa: BLE001, observer lands in P2; until then tests grade 'na'
        return None


def _apply_verdicts(store, cfg, live_facts: dict) -> None:
    """Grade each executed target against its <target>-conformance catalog, write a per-target
    scorecard, and attach a composite gNB verdict. Silent no-op if `cntc` isn't importable."""
    try:
        from cntc.standards import load_catalog
        from cntc.verdict import evaluate
        from cntc.certification import render_console, render_markdown, render_html
    except ImportError:
        return

    sut = cfg.sut or {}
    rig = {
        "adapter": cfg.ran.adapter,
        "mode": "ran",
        "rig_class": sut.get("rig_class", ""),
        "cpu": sut.get("cpu", ""),
        "ran": live_facts.get("ran", cfg.ran.adapter),
    }
    all_suites = store.suite_dicts()
    verdicts: dict[str, dict] = {}
    for tgt in cfg.targets:
        try:
            catalog = load_catalog(cfgmod.Campaign.profile_for(tgt))
        except FileNotFoundError as e:
            print(f"[cntc] {e}")
            continue
        v = evaluate(all_suites, catalog, rig=rig)
        # Only record targets that actually ran a test this campaign.
        if not any(t["outcome"] in ("pass", "fail", "na") for t in v.get("tests", [])):
            continue
        verdicts[tgt] = v
        print(render_console(v))
        (store.dir / f"scorecard-{tgt}.md").write_text(render_markdown(v))
        (store.dir / f"scorecard-{tgt}.html").write_text(render_html(v))

    if not verdicts:
        print("[cntc] no per-target verdicts produced this campaign.")
        return

    # A single-target run IS that product class, write back its own verdict so the dashboard
    # banner and a later `certify --profile <target>-conformance` agree on one certificate id.
    if len(verdicts) == 1:
        top = next(iter(verdicts.values()))
    else:
        top = _composite(verdicts, rig)
    store.set_verdict(top)
    (store.dir / "scorecard.md").write_text(render_markdown(top))
    (store.dir / "scorecard.html").write_text(render_html(top))
    label = "RAN verdict" if len(verdicts) == 1 else "composite gNB verdict"
    print(f"[cntc] {label}: {top['result']}  (scorecards: {store.dir}/scorecard*.md)")


def _composite(verdicts: dict[str, dict], rig: dict) -> dict:
    """Combine per-class verdicts into one gNB verdict: FAIL if any class FAILs, INCOMPLETE if
    any is INCOMPLETE (and none FAIL), else PASS. The `tests` rows are the union across classes
    so the composite scorecard shows the whole NG-RAN node at once."""
    results = [v["result"] for v in verdicts.values()]
    result = "FAIL" if "FAIL" in results else ("INCOMPLETE" if "INCOMPLETE" in results else "PASS")
    rows, ep, ef, ena, et = [], 0, 0, 0, 0
    categories: dict[str, dict] = {}
    failed, notrun = [], []
    for _tgt, v in verdicts.items():
        for r in v.get("tests", []):
            rows.append(r)
            c = categories.setdefault(r["category"], {"passed": 0, "failed": 0, "na": 0})
            c[{"pass": "passed", "fail": "failed", "na": "na"}[r["outcome"]]] += 1
        e = v["essential"]
        ep += e["passed"]; ef += e["failed"]; ena += e["na"]; et += e["total"]
        failed += v.get("failed_essentials", [])
        notrun += v.get("not_run_essentials", [])
    for c in categories.values():
        judged = c["passed"] + c["failed"]
        c["score"] = round(100 * c["passed"] / judged) if judged else None
    return {
        "framework": "CNTC", "profile": "ran-gnb (composite)",
        "catalog_version": "1.0.0",
        "title": "NG-RAN node (split gNB), composite over " + ", ".join(verdicts),
        "standards": ["3GPP TS 38.413 / 38.473 / 38.463 / 38.331",
                      "3GPP TS 33.511 / 33.523 (SCAS)"],
        "rig": rig, "result": result,
        "gate": {"policy": "all product classes must PASS"},
        "essential": {"passed": ep, "failed": ef, "na": ena, "total": et},
        "failed_essentials": failed, "not_run_essentials": notrun,
        "categories": categories, "warnings": [], "tests": rows,
        "per_target": {t: v["result"] for t, v in verdicts.items()},
    }
