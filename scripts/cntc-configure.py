#!/usr/bin/env python3
"""cntc-configure.py — interactive wizard that builds a CNTC campaign config for YOUR SD-Core UPF.

Auto-detects what it can (UPF pod/namespace, BESS access/core ports, a host N3 interface) via
kubectl/bessctl, prompts for the rest, and writes a ready-to-run configs/<name>.yaml with the
af_packet-tuned defaults and the gotcha-proof settings (n3_remote_mac left blank => live resolve).

    ./scripts/cntc-configure.py                 # interactive
    ./scripts/cntc-configure.py --out configs/my-upf.yaml
Requires: kubectl on PATH + KUBECONFIG set (to auto-detect). You can still answer everything by hand.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML missing — run ./scripts/cntc-prereqs.sh first.")


def sh(cmd: list[str], timeout: int = 10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def ask(prompt: str, default: str = "") -> str:
    d = f" [{default}]" if default else ""
    val = input(f"  {prompt}{d}: ").strip()
    return val or default


def pick(prompt: str, options: list[str], default: str = "") -> str:
    if not options:
        return ask(prompt, default)
    if len(options) == 1:
        print(f"  {prompt}: {options[0]}  (only option — using it)")
        return options[0]
    print(f"  {prompt}:")
    for i, o in enumerate(options, 1):
        print(f"     {i}) {o}")
    raw = input(f"  pick [1-{len(options)}]{' or type a value' if True else ''}"
                f"{f' [{default}]' if default else ''}: ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return raw or default or options[0]


# ---- auto-detection ---------------------------------------------------------

def detect_upf_pods() -> list[tuple[str, str]]:
    """Return [(namespace, pod)] whose name looks like a UPF/BESS."""
    out = sh(["kubectl", "get", "pods", "-A", "-o", "json"])
    pods = []
    if out:
        try:
            for p in json.loads(out).get("items", []):
                name = p["metadata"]["name"]
                if re.search(r"upf|bess", name, re.I):
                    pods.append((p["metadata"]["namespace"], name))
        except Exception:
            pass
    return pods


def detect_bess_ports(ns: str, pod: str, container: str) -> list[str]:
    out = sh(["kubectl", "-n", ns, "exec", pod, "-c", container, "--", "bessctl", "show", "port"])
    # port names appear as "  <name>   Driver ..."
    return re.findall(r"^\s+(\w+)\s+Driver", out, re.M)


def detect_host_ifaces() -> list[str]:
    out = sh(["ip", "-br", "link"])
    ifs = []
    for line in out.splitlines():
        name = line.split()[0].split("@")[0] if line.split() else ""
        if name and not re.match(r"lo|docker|cni|veth|cali|flannel|kube|br-", name):
            ifs.append(name)
    return ifs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="output config path (default configs/<campaign>.yaml)")
    args = ap.parse_args()

    print("\n=== CNTC config wizard — build a campaign config for your SD-Core UPF ===\n")
    have_kubectl = bool(sh(["kubectl", "version", "--client", "-o", "json"]))
    if not have_kubectl:
        print("  ! kubectl/KUBECONFIG not detected — auto-detect disabled; answer everything by hand.\n")

    # --- UPF pod / namespace ---
    pods = detect_upf_pods() if have_kubectl else []
    if pods:
        chosen = pick("UPF pod (namespace/pod)", [f"{ns}/{pod}" for ns, pod in pods])
        ns, pod = chosen.split("/", 1)
    else:
        ns = ask("UPF namespace", "aether-5gc")
        pod = ask("UPF pod name", "upf-0")
    container = ask("bessd container name", "bessd")

    # --- BESS ports ---
    # The config uses the LOGICAL datapath port names (access/core), while `bessctl show port`
    # lists the PMD ports as accessFast/coreFast — so strip the "Fast" suffix and drop control ports.
    raw = detect_bess_ports(ns, pod, container) if have_kubectl else []
    dp_ports = sorted({re.sub(r"Fast$", "", p, flags=re.I) for p in raw
                       if p.lower() not in ("notifycp", "pfcpport")}) or ["access", "core"]
    access_guess = next((p for p in dp_ports if "access" in p.lower()), "access")
    core_guess = next((p for p in dp_ports if "core" in p.lower()), "core")
    n3_iface = pick("BESS N3 (access) port name", dp_ports, access_guess)
    n6_iface = pick("BESS N6 (core) port name", dp_ports, core_guess)

    # --- host gen interface (reaches the UPF N3) ---
    host_ifs = detect_host_ifaces()
    gen_guess = next((i for i in host_ifs if "access" in i.lower()), n3_iface)
    gen_iface = pick("host interface that reaches the UPF N3 (for injection)", host_ifs, gen_guess) \
        if host_ifs else ask("host N3 injection interface", n3_iface)

    # --- addressing ---
    n3_remote_ip = ask("UPF N3/access IP (outer GTP-U dst)", "192.168.252.3")
    ue_pool = ask("pfcpsim UE pool (CIDR)", "10.250.0.0/24")
    ue_ip = ue_pool.split("/")[0].rsplit(".", 1)[0] + ".1"   # first UE in the pool

    # --- meta ---
    campaign = ask("campaign id", "MY-UPF-001")
    cpu = ask("SUT CPU (report only)", "N vCPU (VM)")
    nic = ask("SUT NIC (report only)", "virtio (VM)")

    cfg = {
        "campaign": campaign,
        "reset_between_suites": True,
        "sut": {"cpu": cpu, "nic": nic},
        "upf": {
            "adapter": "sdcore_bess",
            "mode": "af_packet",           # adapter reads the live mode; this is informational
            "n3_iface": n3_iface,
            "n6_iface": n6_iface,
            "namespace": ns,
            "pod": pod,
            "bessd_container": container,
            "gen_iface": gen_iface,
            "n3_remote_ip": n3_remote_ip,
            "n3_remote_mac": "",           # BLANK => resolved LIVE (macvlan MAC changes on restart)
            "gen_cpu_affinity": "auto",
            "ue_ip": ue_ip,
            "burst_pps": 5000,             # af_packet-friendly n3neg send rate
        },
        "suite": "all",
        "profile": "conformance",
        "performance": {
            "control": "pybess", "generator": "tcpreplay",
            "frame_sizes": [128, 512, 1024, 1518],
            "max_rate_mpps": 0.02,           # af_packet ceiling-ish
            "search_resolution_mpps": 0.0005,  # resolve the true (low) NDR, not 0
            "search_max_iters": 8, "trial_duration_s": 5,
            "latency_frame_size": 512, "latency_load_mpps": 0.01, "latency_duration_s": 8,
        },
        "load": {"control": "pfcpsim", "generator": "tcpreplay",
                 "ue_counts": [10, 100, 1000, 5000]},
        "pfcp": {"procedures": ["association", "establish", "modify", "delete", "error_handling"]},
        "n3neg": {"control": "pfcpsim", "generator": "tcpreplay"},
    }

    out = args.out or f"configs/{campaign}.yaml"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print(f"\n  ✔ wrote {out}\n")
    print("  Run it:")
    print(f"    ./scripts/cntc-run-all.sh {out}")
    print(f"    # or step by step: cntc run --config {out} --suite all --campaign {campaign}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
