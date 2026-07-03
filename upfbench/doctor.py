"""`upfbench doctor` — preflight check that a machine is ready to benchmark a UPF.

Installs nothing. It verifies the *software* the framework needs (deps, pfcpsim, TRex,
kubectl) and reports the *hardware/testbed* items it can't auto-provision (hugepages,
SR-IOV VFs, IOMMU) so you know exactly what's still missing on a fresh server. Every check
is best-effort and never raises — a missing tool is a WARN/FAIL line, not a crash.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "fail"
_C = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m"}
_RESET = "\033[0m"
_MARK = {OK: " OK ", WARN: "WARN", FAIL: "FAIL"}

ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], timeout: int = 6) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


# --- individual checks: each returns (label, level, detail) ----------------------
def _check_core_deps():
    missing = [m for m in ("yaml", "jinja2", "scapy") if not _importable(m)]
    if missing:
        return "Python core deps", FAIL, f"missing: {', '.join(missing)} (pip install -e .)"
    return "Python core deps", OK, "PyYAML, Jinja2, scapy"


def _check_dashboard_deps():
    vers = {}
    for m in ("dash", "plotly"):
        v = _version(m)
        if v:
            vers[m] = v
    if len(vers) < 2:
        return "Dashboard deps", WARN, "dash/plotly not installed (pip install --user dash plotly)"
    return "Dashboard deps", OK, ", ".join(f"{k} {v}" for k, v in vers.items())


def _check_pfcpsim():
    base = ROOT / "third_party" / "pfcpsim"
    have = [b for b in ("pfcpsim", "pfcpctl") if (base / b).is_file()]
    if len(have) < 2:
        return "pfcpsim built", WARN, ("not built — cd third_party/pfcpsim && "
                                       "go build -o pfcpsim ./cmd/pfcpsim (needs Go >=1.25)")
    return "pfcpsim built", OK, "third_party/pfcpsim/{pfcpsim,pfcpctl}"


def _check_trex():
    root = os.environ.get("TREX_ROOT") or str(Path.home() / "trex-v3.08")
    if (Path(root) / "t-rex-64").is_file():
        return "TRex", OK, root
    # any trex-* dir under home?
    hits = sorted(Path.home().glob("trex-v*"))
    if hits:
        return "TRex", WARN, f"found {hits[0]} but no t-rex-64 (set trex_root in your config)"
    return "TRex", WARN, "not found at ~/trex-v3.08 (DPDK/XDP modes need it; set trex_root)"


def _check_kubectl():
    if not shutil.which("kubectl"):
        return "kubectl", WARN, "not installed (needed to drive a Kubernetes-deployed UPF)"
    rc, out = _run(["kubectl", "get", "nodes", "--no-headers"])
    if rc == 0:
        n = len([l for l in out.splitlines() if l.strip()])
        return "kubectl", OK, f"reachable ({n} node{'s' if n != 1 else ''})"
    return "kubectl", WARN, "installed but cluster unreachable (set KUBECONFIG)"


def _check_hugepages():
    try:
        info = Path("/proc/meminfo").read_text()
    except OSError:
        return "Hugepages", WARN, "cannot read /proc/meminfo"
    total = free = 0
    for line in info.splitlines():
        if line.startswith("HugePages_Total"):
            total = int(line.split()[1])
        elif line.startswith("HugePages_Free"):
            free = int(line.split()[1])
    if total == 0:
        return "Hugepages", WARN, "none allocated (UPF needs ~2Gi + TRex ~2Gi)"
    lvl = OK if free >= 2 else WARN
    return "Hugepages", lvl, f"{total} total, {free} free"


def _check_vfio():
    rc, out = _run(["bash", "-c",
                    "ls -l /sys/bus/pci/drivers/vfio-pci/ 2>/dev/null | grep -c '0000:'"])
    n = out.strip()
    if rc == 0 and n.isdigit() and int(n) > 0:
        return "SR-IOV vfio-pci", OK, f"{n} device(s) bound to vfio-pci"
    return "SR-IOV vfio-pci", WARN, ("no devices bound to vfio-pci — need >=1 free VF on the "
                                     "access PF for the generator")


def _check_iommu():
    try:
        entries = list(Path("/sys/class/iommu").iterdir())
    except OSError:
        entries = []
    if entries:
        return "IOMMU", OK, f"{len(entries)} group(s) active"
    return "IOMMU", WARN, "no /sys/class/iommu (enable intel_iommu=on iommu=pt in the kernel cmdline)"


CHECKS = [_check_core_deps, _check_dashboard_deps, _check_pfcpsim, _check_trex,
          _check_kubectl, _check_hugepages, _check_vfio, _check_iommu]


def run() -> int:
    print("\n  upfbench doctor — environment preflight\n")
    counts = {OK: 0, WARN: 0, FAIL: 0}
    for check in CHECKS:
        try:
            label, level, detail = check()
        except Exception as e:                       # a check must never crash the report
            label, level, detail = check.__name__, WARN, f"check error: {e}"
        counts[level] += 1
        mark = f"{_C[level]}[{_MARK[level]}]{_RESET}"
        print(f"  {mark}  {label:22} {detail}")
    print(f"\n  Summary: {counts[OK]} OK, {counts[WARN]} warning(s), {counts[FAIL]} failure(s)")
    if counts[FAIL]:
        print("  Core software is missing — run scripts/bootstrap_fresh_vm.sh.\n")
    elif counts[WARN]:
        print("  Tester software is ready. Warnings are testbed-specific (see "
              "docs/dpdk-testing-guide.md §2).\n")
    else:
        print("  All green — ready to benchmark.\n")
    return 1 if counts[FAIL] else 0


def _importable(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def _version(mod: str):
    try:
        import importlib
        return getattr(importlib.import_module(mod), "__version__", "?")
    except Exception:
        return None
