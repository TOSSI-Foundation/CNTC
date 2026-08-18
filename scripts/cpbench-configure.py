#!/usr/bin/env python3
"""cpbench-configure.py: interactive wizard that builds a cpbench control-plane campaign config.

Everything that varies between systems is either AUTO-DETECTED or prompted with a sane default,
then written to a ready-to-run configs/<name>.yaml. Detects whether your 5G core is a
docker-compose or a Kubernetes deployment and asks only what that path needs.

    ./scripts/cpbench-configure.py                       # interactive
    ./scripts/cpbench-configure.py --out configs/my-core.yaml
    ./scripts/cpbench-configure.py --non-interactive     # accept every detected/default value

What it figures out for you:
  docker : AMF N2 address, the bridge + host IP the gNB binds to, capture interface, WebUI URL
  k8s    : namespace, service prefix, pod label prefix, AMF N2 NodePort + node IP, WebUI NodePort
  both   : UERANSIM build dir, subscriber defaults

Anything it gets wrong you can simply edit in the generated YAML, the wizard is a convenience,
the config file is the source of truth. Verify with:  cpbench doctor --config <file>
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML missing, run ./scripts/bootstrap_cpbench.sh first.")

NON_INTERACTIVE = False


def sh(cmd: list[str], timeout: int = 15) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL).stdout.strip()
    except Exception:
        return ""


def ask(prompt: str, default: str = "") -> str:
    if NON_INTERACTIVE:
        print(f"  {prompt}: {default}")
        return default
    d = f" [{default}]" if default else ""
    return input(f"  {prompt}{d}: ").strip() or default


def pick(prompt: str, options: list[str], default: str = "") -> str:
    options = [o for o in options if o]
    if not options:
        return ask(prompt, default)
    if len(options) == 1 or NON_INTERACTIVE:
        chosen = default or options[0]
        print(f"  {prompt}: {chosen}")
        return chosen
    print(f"  {prompt}:")
    for i, o in enumerate(options, 1):
        print(f"     {i}) {o}")
    raw = input(f"  pick [1-{len(options)}] or type a value"
                f"{f' [{default}]' if default else ''}: ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return raw or default or options[0]


# ---- detection: which kind of deployment? -----------------------------------

def docker_available() -> bool:
    return bool(sh(["sudo", "-n", "docker", "ps", "--format", "{{.Names}}"]))


def docker_nf_containers() -> list[str]:
    out = sh(["sudo", "-n", "docker", "ps", "--format", "{{.Names}}"])
    return [n for n in out.splitlines() if n in ("amf", "smf", "nrf", "ausf", "udm")]


def k8s_namespaces_with_core() -> list[str]:
    out = sh(["kubectl", "get", "pods", "-A", "-o", "json"], timeout=25)
    ns = set()
    if out:
        try:
            for p in json.loads(out).get("items", []):
                if re.search(r"amf", p["metadata"]["name"], re.I):
                    ns.add(p["metadata"]["namespace"])
        except Exception:
            pass
    return sorted(ns)


# ---- docker detection -------------------------------------------------------

def docker_ip(container: str) -> str:
    return sh(["sudo", "-n", "docker", "inspect", "-f",
               "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container])


def host_bridge_for(ip: str) -> tuple[str, str]:
    """Given a container IP, find the host bridge on the same /24 and the host's IP on it."""
    if not ip:
        return "", ""
    prefix = ".".join(ip.split(".")[:3])
    for line in sh(["ip", "-br", "addr"]).splitlines():
        parts = line.split()
        if len(parts) >= 3 and any(p.startswith(prefix + ".") for p in parts[2:]):
            return parts[0].split("@")[0], parts[2].split("/")[0]
    return "", ""


# ---- k8s detection ----------------------------------------------------------

def k8s_svc(ns: str, name: str, path: str) -> str:
    return sh(["kubectl", "-n", ns, "get", "svc", name, "-o", f"jsonpath={{{path}}}"])


def k8s_detect(ns: str) -> dict:
    """Derive service prefix, pod label prefix, AMF N2 NodePort, WebUI NodePort, node IP."""
    d = {"namespace": ns}
    svcs = sh(["kubectl", "-n", ns, "get", "svc", "-o", "name"]).splitlines()
    svcs = [s.split("/", 1)[-1] for s in svcs]
    amf_svc = next((s for s in svcs if s.endswith("-amf-service")), "")
    d["svc_prefix"] = amf_svc[: -len("-amf-service")] if amf_svc else "free5gc-helm-free5gc"
    # pod label value prefix (chart labels pods app.kubernetes.io/name=<prefix><nf>)
    lbl = sh(["kubectl", "-n", ns, "get", "pods", "-l", "app.kubernetes.io/name",
              "-o", "jsonpath={.items[*].metadata.labels.app\\.kubernetes\\.io/name}"])
    names = [v for v in lbl.split() if v.endswith("amf")]
    d["nf_label_prefix"] = names[0][: -len("amf")] if names else "free5gc-"
    # AMF N2 NodePort
    n2 = next((s for s in svcs if "amf" in s and "n2" in s), "")
    d["amf_n2_port"] = k8s_svc(ns, n2, ".spec.ports[0].nodePort") if n2 else ""
    # WebUI NodePort
    web = next((s for s in svcs if "webui" in s and "service" in s), "")
    d["webui_port"] = k8s_svc(ns, web, ".spec.ports[0].nodePort") if web else ""
    # node InternalIP
    node_ip = sh(["kubectl", "get", "nodes", "-o",
                  "jsonpath={.items[0].status.addresses[?(@.type=='InternalIP')].address}"])
    d["node_ip"] = node_ip
    pods = sh(["kubectl", "-n", ns, "get", "pods", "-o", "name"]).splitlines()
    pods = [p.split("/", 1)[-1] for p in pods]
    # NB: match on an explicit "-ue" component, a bare "ue" substring also matches "UEransim-gnb"
    d["ue_pod_match"] = next((p.rsplit("-", 2)[0] for p in pods
                              if re.search(r"(^|-)ue(-|$)", p.rsplit("-", 2)[0])), "ueransim-ue")
    d["gnb_pod_match"] = next((p.rsplit("-", 2)[0] for p in pods if "gnb" in p), "ueransim-gnb")
    return d


def find_ueransim() -> str:
    for cand in (Path.home() / "UERANSIM", Path("/opt/UERANSIM")):
        if (cand / "build" / "nr-gnb").exists():
            return str(cand)
    return str(Path.home() / "UERANSIM")


# ---- main -------------------------------------------------------------------

def main() -> int:
    global NON_INTERACTIVE
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="output path (default configs/<campaign>.yaml)")
    ap.add_argument("--non-interactive", action="store_true",
                    help="accept every detected/default value without prompting")
    args = ap.parse_args()
    NON_INTERACTIVE = args.non_interactive

    print("\n=== cpbench config wizard, build a control-plane campaign for YOUR 5G core ===\n")

    # --- which deployment? ---
    kinds = []
    if docker_nf_containers():
        kinds.append("docker")
    if k8s_namespaces_with_core():
        kinds.append("k8s")
    if not kinds:
        print("  ! no running free5GC detected (docker or k8s), you can still answer by hand.\n")
        kinds = ["docker", "k8s"]
    kind = pick("deployment type", kinds, kinds[0])

    campaign = ask("campaign id", f"FREE5GC-{kind.upper()}-001")
    target_nf = pick("which NF(s) to test", ["all", "amf", "smf", "nrf", "ausf", "udm"], "all")

    core: dict = {}
    drivers: dict = {}

    if kind == "docker":
        core["adapter"] = "free5gc"
        core["compose_dir"] = ask("free5GC compose dir", str(Path.home() / "free5gc-compose"))
        core["sudo"] = True
        core["webui"] = ask("WebUI URL (subscriber provisioning)", "http://localhost:5000")
        amf_ip = docker_ip("amf")
        bridge, host_ip = host_bridge_for(amf_ip)
        print(f"  (detected: AMF={amf_ip or '?'}  bridge={bridge or '?'}  host IP={host_ip or '?'})")
        drivers["gnb"] = "ueransim"
        drivers["amf_n2_addr"] = ask("AMF N2 address (SCTP 38412)", amf_ip)
        drivers["amf_n2_port"] = int(ask("AMF N2 port", "38412") or 38412)
        drivers["gnb_link_ip"] = ask("gNB link IP (host IP on the core bridge)", host_ip)
        drivers["n2_iface"] = ask("interface to capture N2/N4 on", bridge or "any")
    else:
        core["adapter"] = "free5gc_k8s"
        nss = k8s_namespaces_with_core()
        ns = pick("namespace", nss, nss[0] if nss else "free5gc")
        k = k8s_detect(ns)
        print(f"  (detected: svc_prefix={k['svc_prefix']}  label_prefix={k['nf_label_prefix']}  "
              f"node={k['node_ip'] or '?'}  N2 NodePort={k['amf_n2_port'] or '?'})")
        core["namespace"] = ns
        core["svc_prefix"] = ask("service name prefix", k["svc_prefix"])
        core["nf_label"] = "app.kubernetes.io/name"
        core["nf_label_prefix"] = ask("pod label value prefix", k["nf_label_prefix"])
        core["webui"] = ask("WebUI URL (NodePort)",
                            f"http://localhost:{k['webui_port']}" if k["webui_port"] else "http://localhost:30500")
        drivers["gnb"] = "ueransim_k8s"
        drivers["ue_pod_match"] = ask("UE pod name substring", k["ue_pod_match"])
        drivers["gnb_pod_match"] = ask("gNB pod name substring", k["gnb_pod_match"])
        drivers["amf_n2_addr"] = ask("AMF N2 address (node IP for the NodePort)", k["node_ip"])
        drivers["amf_n2_port"] = int(ask("AMF N2 NodePort", k["amf_n2_port"] or "31412") or 31412)
        drivers["n2_iface"] = ask("interface to capture N2/N4 on (node)", "any")
        drivers["k8s_drive"] = (ask("drive the in-cluster UE (release/deregister + fresh attach)? "
                                    "[yes/no]", "yes").lower().startswith("y"))

    drivers["ueransim_dir"] = ask("UERANSIM directory", find_ueransim())

    # --- subscriber (must match the UE) ---
    print("\n  Subscriber (must match your UE's SIM credentials):")
    sub = {
        "supi": ask("SUPI", "imsi-208930000000001"),
        "ki": ask("Ki", "8baf473f2f8fd09487cccbd7097c6862"),
        "opc": ask("OPc", "8e27b6af0e692e750f32667a3b14605d"),
        "dnn": ask("DNN", "internet"),
    }
    plmn = ask("PLMN (mcc+mnc)", "20893")
    if plmn != "20893":
        sub["plmn"] = plmn

    cfg = {
        "domain": "control-plane",
        "campaign": campaign,
        "target_nf": target_nf,
        "core": core,
        "drivers": drivers,
        "subscribers": [sub],
        "sut": {
            "rig_class": ask("rig class (free-form label for baseline comparison)",
                             "k8s" if kind == "k8s" else "docker-vm"),
            "core_release": ask("core release label", "free5GC"),
        },
    }

    out = Path(args.out or f"configs/{campaign.lower()}.yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# Generated by scripts/cpbench-configure.py, edit freely; this file is the\n"
                   "# source of truth. Re-check with: cpbench doctor --config " + str(out) + "\n"
                   + yaml.safe_dump(cfg, sort_keys=False, width=100))
    print(f"\n  wrote {out}\n")
    print("  Next:")
    print(f"    cpbench doctor --config {out}      # must say READY")
    print(f"    cpbench run    --config {out} --nf all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
