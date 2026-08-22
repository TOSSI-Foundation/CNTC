"""ranbench doctor, a real preflight so a run fails *here* with a clear message, not mid-test.

Checks, in order, everything a RAN campaign depends on:
  1. Python deps    (yaml)
  2. External CLIs  (tcpdump/tshark for the wire evidence, pgrep/ss for liveness, sudo)
  3. The RAN stack  (binaries built, which product classes are running, release)
  4. Endpoints      (each target's control/data socket resolves from its live config)
  5. Evidence       (per-interface pcaps enabled, without them the protocol tests can't judge)
  6. The core peer  (an AMF for N2 / UPF for N3) and subscribers for 5G-AKA

Returns non-zero if any HARD check fails, so it is CI-friendly. Nothing here mutates the RAN.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from cntc_common.results import Store
from ranbench import config as cfgmod
from ranbench.adapters.base import load_adapter


def _check_python_deps() -> list[tuple[str, bool, str]]:
    out = []
    for mod, why in [("yaml", "campaign + RAN config parsing")]:
        try:
            __import__(mod)
            out.append((f"py:{mod}", True, "installed"))
        except ImportError:
            out.append((f"py:{mod}", False, f"MISSING, {why}  (pip install -e .)"))
    return out


def _check_external() -> list[tuple[str, bool, str]]:
    out = []
    for tool, why in [("tcpdump", "capture F1-C/E1/N2/N3"), ("tshark", "decode F1AP/E1AP/NGAP"),
                      ("pgrep", "product-class liveness"), ("sudo", "privileged capture")]:
        p = shutil.which(tool)
        out.append((f"cli:{tool}", bool(p), p or f"MISSING on PATH, needed to {why}"))
    return out


def run(config_path: str) -> int:
    cfg = cfgmod.load(config_path)
    store = Store(Path("campaigns"), f"_doctor-{cfg.campaign}")
    ran = load_adapter(cfg.ran.adapter, cfg, store)

    print(f"ranbench doctor, ran={cfg.ran.adapter}  target={cfg.target}")
    rows: list[tuple[str, bool, str]] = []
    rows += _check_python_deps()
    rows += _check_external()

    # the RAN stack itself
    try:
        facts = ran.describe()
        built = str(facts.get("nodes_built", ""))
        up = str(facts.get("nodes_up", ""))
        rows.append(("ran:build", bool(built) and not built.startswith("(none"),
                     f"{facts.get('ran','?')}, built: {built or '?'}"))
        rows.append(("ran:release", bool(facts.get("ran_release")),
                     f"{facts.get('ran_release','unknown')}, running now: {up or 'none'}"))
    except Exception as e:  # noqa: BLE001
        rows.append(("ran:build", False, f"describe failed: {e}"))

    # per-target endpoint resolution + evidence
    for tgt in cfg.targets:
        try:
            ep = ran.node_endpoint(tgt)
        except Exception as e:  # noqa: BLE001
            rows.append((f"ep:{tgt}", False, f"error: {e}"))
            continue
        rows.append((f"ep:{tgt}", bool(ep), ep or "unresolved, is the config path set in ran.configs?"))
        # Not running is fine: the run starts each product itself and stops it again so the
        # pcaps flush. What would be fatal is being unable to *observe* liveness at all, since
        # the robustness cases judge crashes on it.
        alive = ran.node_alive(tgt)
        rows.append((f"alive:{tgt}", alive is not None,
                     "running" if alive else ("not running (the run will start it)"
                                              if alive is False else
                                              "CANNOT OBSERVE, crash detection impossible")))
        pcaps = ran.pcap_paths(tgt)
        rows.append((f"pcap:{tgt}", bool(pcaps),
                     ", ".join(sorted(pcaps)) if pcaps else
                     "no pcaps enabled, protocol/security tests will grade 'na'"))

    # the core peer
    core_ok = bool(cfg.core.adapter)
    rows.append(("core", core_ok,
                 f"{cfg.core.adapter} (N2/N3 peer)" if core_ok else
                 "no core.adapter set, the RAN has nothing to attach to"))

    n = len(cfg.subscribers)
    rows.append(("subscribers", n > 0, f"{n} in config" if n else "none, 5G-AKA will fail"))

    # Contact the core rather than trusting the config. Both of these break the run in ways that
    # look like RAN faults: an unreachable AMF stops the attach at NG Setup, and a UDM that
    # cannot serve subscription data lets registration succeed and then refuses the PDU session.
    if core_ok:
        core = None
        try:
            from ranbench.drivers.base import load_driver
            core = load_driver(f"core_{cfg.core.adapter}", cfg,
                               Store(Path(tempfile.mkdtemp()), "doctor"))
        except Exception as e:  # noqa: BLE001
            rows.append(("core driver", False, f"could not load: {e}"))
        if core is not None:
            caps = set()
            try:
                caps = core.capabilities()
            except Exception:  # noqa: BLE001
                pass
            if "amf_reachable" not in caps:
                rows.append(("core probe", True,
                             f"{cfg.core.adapter} exposes no live probes; core readiness is "
                             f"the tester's to confirm"))
            else:
              try:
                reach = core.amf_reachable()
                rows.append(("amf n2", reach is True,
                             f"{core.amf_n2_endpoint()} accepting SCTP" if reach is True else
                             f"{core.amf_n2_endpoint() or 'AMF'} not accepting, NG Setup "
                             f"will fail and no test can be judged"))
              except Exception as e:  # noqa: BLE001
                rows.append(("amf n2", False, f"probe failed: {e}"))
            if cfg.subscribers and "subscriber_data_ready" in core.capabilities():
                try:
                    ready, detail = core.subscriber_data_ready(cfg.subscribers[0])
                    rows.append(("subscriber data", ready is True, detail))
                except Exception as e:  # noqa: BLE001
                    rows.append(("subscriber data", False, f"probe failed: {e}"))

    hard_fail = 0
    for name, ok, detail in rows:
        mark = "ok  " if ok else "FAIL"
        if not ok:
            hard_fail += 1
        print(f"  [{mark}] {name:<16} {detail}")

    ready = hard_fail == 0
    print(f"\n  RESULT: {'READY' if ready else f'NOT READY ({hard_fail} check(s) failed)'}")
    return 0 if ready else 1
