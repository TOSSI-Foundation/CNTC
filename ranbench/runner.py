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

import contextlib
import signal
from pathlib import Path

from cntc_common.results import Store, SuiteResult, TestResult
from ranbench import config as cfgmod
from ranbench.adapters.base import load_adapter
from ranbench.drivers.base import load_driver
from ranbench.suites.base import RunContext
from ranbench.suites.registry import build_suite, TARGET_REQUIRES


@contextlib.contextmanager
def _one_shot_interrupt():
    """Let the first Ctrl-C stop the run, and ignore every one after it.

    An operator pressing Ctrl-C repeatedly is the normal case, and each extra signal used to
    land in the middle of teardown, leaving the RAN processes alive and the results unsaved.
    The first interrupt raises as usual; the handler then disarms itself so shutdown always
    completes.
    """
    def handler(signum, frame):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGINT, signal.SIG_IGN)   # disarm before unwinding
        raise KeyboardInterrupt
    try:
        previous = signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):        # not on the main thread
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGINT, previous)


@contextlib.contextmanager
def _uninterruptible():
    """Ignore Ctrl-C for the duration of a block.

    Teardown must finish even when the operator is impatient. A second Ctrl-C landing in the
    middle of cleanup is what leaves the RAN processes running and the captures truncated, so
    the interrupt is held off until the products are down.
    """
    try:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):        # not on the main thread; nothing to do
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGINT, previous)


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
    interrupted = False
    try:
      with _one_shot_interrupt():
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
    except KeyboardInterrupt:
        # Stop cleanly rather than abandoning a half-started RAN. Whatever ran already is kept
        # and graded, clearly marked as an interrupted campaign.
        interrupted = True
        print("\n[ranbench] interrupted, shutting the RAN down cleanly, please wait")
    finally:
        with _uninterruptible():
            _shutdown(ran, cfg)
            try:
                ran.teardown()
            except Exception:  # noqa: BLE001
                pass

    with _uninterruptible():
        _diagnose(store, cfg, ran)
        _apply_verdicts(store, cfg, live_facts)
        results_path = store.save(sut=cfg.sut,
                                  status="interrupted" if interrupted else "complete")
    if interrupted:
        print(f"[ranbench] partial results (campaign stopped early): {results_path}")
    else:
        print(f"[ranbench] results: {results_path}")
    return results_path


def _shutdown(ran, cfg) -> None:
    """Stop the UE and every product class, whatever state the run was in."""
    import subprocess
    with contextlib.suppress(Exception):
        subprocess.run(["sudo", "-n", "pkill", "-x", "nr-uesoftmodem"],
                       capture_output=True, timeout=15)
    for tgt in ("du", "cuup", "cucp"):
        with contextlib.suppress(Exception):
            ran.stop(tgt)
    left = []
    for tgt in ("du", "cuup", "cucp"):
        with contextlib.suppress(Exception):
            if ran.node_alive(tgt) is True:
                left.append(tgt)
    if left:
        print(f"[ranbench] still running after shutdown: {', '.join(left)}")


def _diagnose(store, cfg, ran) -> None:
    """Attach a probable cause to every failing or unevaluated result.

    The verdict stays exactly what was observed; this only explains it. Collected once, after
    the suites have run, so the facts describe the state the tests were judged in.
    """
    try:
        from ranbench import diagnostics
    except ImportError:
        return
    obs = None
    try:
        from ranbench.drivers.ue_oai_zmq import _OBS_CACHE
        obs = next(iter(_OBS_CACHE.values()), None)
    except Exception:  # noqa: BLE001
        pass
    try:
        facts = diagnostics.collect(cfg, ran, obs)
    except Exception as e:  # noqa: BLE001, diagnosis must never break a run
        print(f"[ranbench] warning: could not collect diagnostics: {e}")
        return
    store.set_sut_live({"diagnostics": "collected"})
    n = 0
    for sres in store._suites:  # noqa: SLF001, annotating our own results in place
        for tr in sres.tests:
            cause = diagnostics.probable_cause(tr.id, tr.status, tr.notes or "", facts)
            if cause:
                tr.metrics = dict(tr.metrics or {})
                tr.metrics["probable_cause"] = cause
                tr.notes = f"{tr.notes}  |  Probable cause: {cause}" if tr.notes else \
                           f"Probable cause: {cause}"
                n += 1
    if n:
        print(f"[ranbench] diagnosed {n} result(s) with a probable cause")


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
    _print_problems(store, top)


def _print_problems(store, verdict: dict) -> None:
    """What is actually wrong with this RAN, grouped by cause rather than by test.

    Ten failing tests caused by one unreachable peer is one problem, not ten, and an operator
    needs to be told the problem."""
    try:
        from ranbench import diagnostics
    except ImportError:
        return
    by_id = {}
    for sres in store._suites:  # noqa: SLF001
        for tr in sres.tests:
            # a diagnosed cause if there is one, otherwise the test's own note, which for the
            # self-explaining cases (no IPsec, null algorithm) is already the real reason. The
            # verdict's "status == fail" is never useful here.
            cause = (tr.metrics or {}).get("probable_cause")
            if not cause and tr.notes:
                cause = tr.notes.split("  |  Probable cause:")[0].split("[TS")[0].strip()
            by_id[tr.id] = cause
    rows = []
    for r in verdict.get("tests", []):
        rows.append({**r, "cause": by_id.get(r["id"])})
    lines = diagnostics.summary({}, rows)
    if not lines:
        return
    print("\n  PROBLEMS FOUND")
    for line in lines:
        print(f"    - {line}")


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
