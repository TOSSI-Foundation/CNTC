"""Orchestrator: wire the plugins, run the chosen NF suite(s), store + grade + scorecard.

The control-plane analog of ``upfbench.runner``:
  1. load the core adapter                 (adapters/<name>.py)
  2. read live SUT facts + provision subscribers
  3. for each target NF -> wire driver(s)/observer per registry.NF_REQUIRES
  4. run each NfTestCase (real or StubCase), collect TestResult -> SuiteResult(suite=nf)
  5. store results.json; grade each NF against its <nf>-conformance catalog; write scorecards
     + a composite control-plane verdict

Phase 2.0 runs end-to-end with StubCase tests (status='not_implemented' -> graded 'na'),
proving the pipeline and producing per-NF scorecards + a composite INCOMPLETE verdict.
"""
from __future__ import annotations

from pathlib import Path

from cntc_common.results import Store, SuiteResult, TestResult
from cpbench import config as cfgmod
from cpbench.adapters.base import load_adapter
from cpbench.drivers.base import load_driver
from cpbench.suites.base import RunContext
from cpbench.suites.registry import build_suite, NF_REQUIRES


def run(config_path: str, campaigns_root: str = "campaigns", nf: str | None = None,
        campaign: str | None = None) -> Path:
    cfg = cfgmod.load(config_path)
    if nf:                          # CLI --nf overrides the config
        if nf not in cfgmod.VALID_TARGETS:
            raise ValueError(f"--nf must be one of {cfgmod.VALID_TARGETS}")
        cfg.target_nf = nf
    if campaign:
        cfg.campaign = campaign
    store = Store(Path(campaigns_root), cfg.campaign)

    core = load_adapter(cfg.core.adapter, cfg, store)
    core.deploy()
    live_facts: dict = {}
    try:
        live_facts = core.describe()
        store.set_sut_live(live_facts)
    except Exception as e:  # noqa: BLE001 — surface as a note, don't abort
        print(f"[cpbench] warning: could not read core facts: {e}")
        store.set_sut_live({"core_probe_error": str(e)})

    if cfg.subscribers:
        try:
            prov = core.provision_subscribers(cfg.subscribers)
            print(f"[cpbench] subscribers: {prov}")
        except Exception as e:  # noqa: BLE001
            print(f"[cpbench] warning: subscriber provisioning failed: {e}")

    store.save(sut=cfg.sut, status="running")   # LIVE: appear on the dashboard immediately
    try:
        for nf_name in cfg.target_nfs:
            print(f"[cpbench] testing NF: {nf_name}")
            store.save(sut=cfg.sut, status="running", running_suite=nf_name)
            req = NF_REQUIRES.get(nf_name, {})
            driver = _maybe_driver(cfg.drivers.get("gnb") or req.get("gnb"), cfg, store)
            sbi = _maybe_driver("sbi_client", cfg, store) if req.get("sbi") else None
            observer = _maybe_observer(cfg, store) if req.get("observer") else None
            endpoint = ""
            try:
                endpoint = core.nf_endpoint(nf_name)
            except Exception as e:  # noqa: BLE001
                print(f"[cpbench] warning: could not resolve {nf_name} endpoint: {e}")

            for d in (driver, sbi):
                if d is not None:
                    try:
                        d.setup()
                    except Exception as e:  # noqa: BLE001
                        print(f"[cpbench] warning: driver {d.name} setup failed: {e}")

            ctx = RunContext(cfg=cfg, core=core, driver=driver, sbi=sbi, observer=observer,
                             store=store, nf=nf_name, endpoint=endpoint, knobs=cfg.knobs.get(nf_name, {}))
            sres = SuiteResult(suite=nf_name)
            try:
                for case in build_suite(nf_name):
                    print(f"  - {case.id} {case.name}")
                    try:
                        sres.tests.append(case.run(ctx))
                    except Exception as e:  # noqa: BLE001 — one bad test must not kill the suite
                        msg = f"{type(e).__name__}: {e}"
                        print(f"    ! {case.id} errored: {msg}")
                        sres.tests.append(TestResult(case.id, case.name, "error", notes=msg))
            finally:
                for d in (driver, sbi):
                    if d is not None:
                        try:
                            d.teardown()
                        except Exception:  # noqa: BLE001
                            pass
            store.add_suite(sres)
            store.save(sut=cfg.sut, status="running")
    finally:
        core.teardown()

    _apply_verdicts(store, cfg, live_facts)
    results_path = store.save(sut=cfg.sut)
    print(f"[cpbench] results: {results_path}")
    return results_path


def _maybe_driver(name, cfg, store):
    if not name:
        return None
    try:
        return load_driver(name, cfg, store)
    except Exception as e:  # noqa: BLE001
        print(f"[cpbench] warning: driver {name!r} unavailable: {e}")
        return None


def _maybe_observer(cfg, store):
    try:
        from cpbench.observers.nas_ngap import NasNgapObserver
        return NasNgapObserver(cfg, store)
    except Exception:  # noqa: BLE001
        return None


def _apply_verdicts(store, cfg, live_facts: dict) -> None:
    """Grade each executed NF against its <nf>-conformance catalog, write a per-NF scorecard,
    and attach a composite control-plane verdict. Silent no-op if `cntc` isn't importable."""
    try:
        from cntc.standards import load_catalog
        from cntc.verdict import evaluate
        from cntc.certification import render_console, render_markdown, render_html
    except ImportError:
        return

    sut = cfg.sut or {}
    rig = {
        "adapter": cfg.core.adapter,
        "mode": "control-plane",
        "rig_class": sut.get("rig_class", ""),
        "cpu": sut.get("cpu", ""),
        "core": live_facts.get("core", cfg.core.adapter),
    }
    all_suites = store.suite_dicts()
    verdicts: dict[str, dict] = {}
    for nf_name in cfg.target_nfs:
        try:
            catalog = load_catalog(cfgmod.Campaign.profile_for(nf_name))
        except FileNotFoundError as e:
            print(f"[cntc] {e}")
            continue
        v = evaluate(all_suites, catalog, rig=rig)
        # Only record NFs that actually ran a test this campaign.
        if not any(t["outcome"] in ("pass", "fail", "na") for t in v.get("tests", [])):
            continue
        verdicts[nf_name] = v
        print(render_console(v))
        (store.dir / f"scorecard-{nf_name}.md").write_text(render_markdown(v))
        (store.dir / f"scorecard-{nf_name}.html").write_text(render_html(v))

    if not verdicts:
        print("[cntc] no per-NF verdicts produced this campaign.")
        return

    # When a single NF ran, the campaign IS that NF — write back its own <nf>-conformance
    # verdict (not a composite-of-one wrapper). This keeps the top-level verdict, the dashboard
    # certificate banner, and a later `certify --profile <nf>-conformance` all consistent — the
    # same profile, the same certificate ID. Only a multi-NF run needs the composite.
    if len(verdicts) == 1:
        top = next(iter(verdicts.values()))
    else:
        top = _composite(verdicts, rig)
    store.set_verdict(top)
    (store.dir / "scorecard.md").write_text(render_markdown(top))
    (store.dir / "scorecard.html").write_text(render_html(top))
    label = "control-plane verdict" if len(verdicts) == 1 else "composite control-plane verdict"
    print(f"[cntc] {label}: {top['result']}  (scorecards: {store.dir}/scorecard*.md)")


def _composite(verdicts: dict[str, dict], rig: dict) -> dict:
    """Combine per-NF verdicts into one control-plane verdict: FAIL if any NF FAILs,
    INCOMPLETE if any is INCOMPLETE (and none FAIL), else PASS. The `tests` rows are the
    union across NFs so the composite scorecard shows the whole control plane at once."""
    results = [v["result"] for v in verdicts.values()]
    result = "FAIL" if "FAIL" in results else ("INCOMPLETE" if "INCOMPLETE" in results else "PASS")
    rows, ep, ef, ena, et = [], 0, 0, 0, 0
    categories: dict[str, dict] = {}
    failed, notrun = [], []
    for nf_name, v in verdicts.items():
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
        "framework": "CNTC", "profile": "cp-control-plane (composite)",
        "catalog_version": "0.1.0",
        "title": "5G Core Control-Plane — composite over " + ", ".join(verdicts),
        "standards": ["3GPP TS 24.501 / 38.413 / 29.5xx", "3GPP TS 33.51x (SCAS)"],
        "rig": rig, "result": result,
        "gate": {"policy": "all NFs must PASS"},
        "essential": {"passed": ep, "failed": ef, "na": ena, "total": et},
        "failed_essentials": failed, "not_run_essentials": notrun,
        "categories": categories, "warnings": [], "tests": rows,
        "per_nf": {nf: v["result"] for nf, v in verdicts.items()},
    }
