"""cpbench doctor — a real preflight so a run fails *here* with a clear message, not mid-test.

Checks, in order, everything cpbench depends on on a fresh box:
  1. Python deps   (httpx+h2 for SBI, yaml)
  2. External CLIs (docker/sudo/ping/pkill — used by the free5gc adapter + UERANSIM driver)
  3. UERANSIM build (nr-gnb/nr-ue) if a gnb driver is configured
  4. The core       (NF containers up, target-NF endpoints resolve)
  5. Addressing     (amf_n2_addr / gnb_link_ip set — no silent localhost defaults)
  6. Subscribers    (5G-AKA needs a provisioned SIM)

Returns non-zero if any HARD check fails, so it is CI-friendly. Nothing here mutates the core.
"""
from __future__ import annotations

import shutil
import socket
from pathlib import Path

from cpbench import config as cfgmod
from cpbench.adapters.base import load_adapter
from cntc_common.results import Store


def _check_python_deps() -> list[tuple[str, bool, str]]:
    out = []
    for mod, why in [("httpx", "SBI client (NRF/AUSF/UDM + NF security tests)"),
                     ("h2", "HTTP/2 for SBI"), ("yaml", "config + UERANSIM config generation")]:
        try:
            __import__(mod)
            out.append((f"py:{mod}", True, "installed"))
        except ImportError:
            out.append((f"py:{mod}", False, f"MISSING — {why}  (pip install -e .)"))
    return out


def _check_external(cfg) -> list[tuple[str, bool, str]]:
    out = []
    needs = ["docker", "sudo", "ping", "pkill"]
    for t in needs:
        p = shutil.which(t)
        out.append((f"cli:{t}", bool(p), p or "MISSING on PATH"))
    return out


def _check_ueransim(cfg) -> list[tuple[str, bool, str]]:
    gnb = cfg.drivers.get("gnb")
    if gnb != "ueransim":
        return []
    d = cfgmod.expand_user_path(cfg.drivers.get("ueransim_dir", "~/UERANSIM"))
    built = (d / "build" / "nr-gnb").exists() and (d / "build" / "nr-ue").exists()
    return [("ueransim", built, f"{d}/build" + ("" if built else "  — nr-gnb/nr-ue NOT built"))]


def _tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run(config_path: str) -> int:
    cfg = cfgmod.load(config_path)
    store = Store(Path("campaigns"), f"_doctor-{cfg.campaign}")
    core = load_adapter(cfg.core.adapter, cfg, store)

    print(f"cpbench doctor — core={cfg.core.adapter}  target={cfg.target_nf}")
    rows: list[tuple[str, bool, str]] = []
    rows += _check_python_deps()
    rows += _check_external(cfg)
    rows += _check_ueransim(cfg)

    # core liveness
    try:
        facts = core.describe()
        up = facts.get("nfs_up", "")
        core_ok = bool(up) and not str(up).startswith("(none")
        rows.append(("core", core_ok, f"{facts.get('core','?')} — NFs up: {up or '?'}"))
    except Exception as e:  # noqa: BLE001
        rows.append(("core", False, f"describe failed: {e}"))

    # per-NF endpoint resolution
    for nf in cfg.target_nfs:
        try:
            ep = core.nf_endpoint(nf)
        except Exception as e:  # noqa: BLE001
            ep = ""
            rows.append((f"ep:{nf}", False, f"error: {e}"))
            continue
        ok = bool(ep)
        if ok and ":" in ep:
            host, _, port = ep.partition(":")
            ok = _tcp_open(host, int(port or 0))
        rows.append((f"ep:{nf}", ok, ep or "unresolved"))

    # addressing (UERANSIM N2) — flag silent localhost defaults on a non-local core
    if cfg.drivers.get("gnb") == "ueransim":
        amf = cfg.drivers.get("amf_n2_addr", "")
        link = cfg.drivers.get("gnb_link_ip", "")
        rows.append(("addr:amf_n2", bool(amf), amf or "unset — set drivers.amf_n2_addr"))
        rows.append(("addr:gnb_link", bool(link), link or "unset — set drivers.gnb_link_ip"))

    # subscribers
    n = len(cfg.subscribers)
    rows.append(("subscribers", n > 0, f"{n} in config" if n else "none — 5G-AKA will fail"))

    hard_fail = 0
    for name, ok, detail in rows:
        mark = "ok  " if ok else "FAIL"
        if not ok:
            hard_fail += 1
        print(f"  [{mark}] {name:<16} {detail}")

    ready = hard_fail == 0
    print(f"\n  RESULT: {'READY' if ready else f'NOT READY ({hard_fail} check(s) failed)'}")
    return 0 if ready else 1
