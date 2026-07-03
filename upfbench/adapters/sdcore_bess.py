"""SD-Core / OMEC BESS-UPF adapter (the first UPF).

Connect-only: the UPF is assumed already deployed (via aether-onramp). The BESS
dataplane runs inside a Kubernetes pod (default ``upf-0`` / container ``bessd`` in
namespace ``aether-5gc``), so this adapter reads facts and counters by shelling into
that container with ``kubectl exec ... -- bessctl ...``. All commands are captured into
the result store for the report's reproducibility appendix.

Config knobs (campaign YAML ``upf.extra``, all optional — defaults shown)::

    upf:
      adapter: sdcore_bess
      n3_iface: access          # BESS access port name (N3 ingress)
      n6_iface: core            # BESS core port name   (N6 egress)
      namespace: aether-5gc     # k8s namespace of the UPF pod
      pod: upf-0                # UPF pod name
      bessd_container: bessd    # container running bessd
      kubectl: kubectl          # kubectl binary (or full path)
      kubeconfig: ""            # KUBECONFIG path; "" = kubectl default (~/.kube/config)
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any

from upfbench.adapters.base import UPFAdapter

# Path of the UPF config inside the bessd container (mounted from the `upf` configmap).
_UPF_JSONC = "/etc/bess/conf/upf.jsonc"

# A port block in `bessctl show port` starts with: "<name>  Driver <drv>  HWaddr <mac> ..."
_PORT_HDR = re.compile(r"^\s*(\S+)\s+Driver\s+(\S+)\s+HWaddr\s+(\S+)")
_NUM = re.compile(r"(\w+):\s*([\d,]+)")


class Adapter(UPFAdapter):
    name = "sdcore_bess"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.extra
        self.namespace = e.get("namespace", "aether-5gc")
        self.pod = e.get("pod", "upf-0")
        self.container = e.get("bessd_container", "bessd")
        self.kubectl = e.get("kubectl", "kubectl")
        self.kubeconfig = e.get("kubeconfig", "")
        self.grpc = e.get("bess_grpc", "localhost:10514")
        self.bess_path = e.get("bess_path", "/opt/bess")
        # BESS port names for N3/N6; fall back to the BESS-UPF defaults.
        self.n3_port = cfg.n3_iface or "access"
        self.n6_port = cfg.n6_iface or "core"
        self._script_n = 0
        self._lat_restore: dict | None = None   # how to rewire after a latency probe
        self._sc_restore: dict | None = None     # how to rewire after an egress short-circuit

    # --- command plumbing -----------------------------------------------------
    def _kubectl_base(self) -> list[str]:
        cmd = [self.kubectl]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        return cmd

    def _exec(self, *argv: str) -> str:
        """Run a command inside the bessd container; return stdout (captured)."""
        cmd = [*self._kubectl_base(), "exec", "-n", self.namespace, self.pod,
               "-c", self.container, "--", *argv]
        self.store.record_command(" ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"kubectl exec failed ({proc.returncode}): {' '.join(argv)}\n"
                f"{proc.stderr.strip()}")
        return proc.stdout

    def _bessctl(self, *argv: str) -> str:
        return self._exec("bessctl", *argv)

    # --- reset -> fresh bessd datapath (runbook §7) ---------------------------
    def reset(self) -> None:
        """Delete the UPF pod so the StatefulSet recreates a fresh bessd, then block
        until it's Ready. A saturating performance run can crash bessd (Exit 1) and
        leave the datapath forwarding 0; a clean pod restores it."""
        base = self._kubectl_base()
        dele = [*base, "delete", "pod", self.pod, "-n", self.namespace, "--wait=true"]
        self.store.record_command(" ".join(dele))
        subprocess.run(dele, capture_output=True, text=True)
        wait = [*base, "wait", "--for=condition=ready", f"pod/{self.pod}",
                "-n", self.namespace, "--timeout=180s"]
        self.store.record_command(" ".join(wait))
        proc = subprocess.run(wait, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"upf-0 not Ready after reset: {proc.stderr.strip()}")
        # let bessd bring its ports up + the pfcp-agent re-bind N4 before we drive it
        time.sleep(8)

    # --- introspection -> report SUT section ----------------------------------
    def describe(self) -> dict[str, Any]:
        """SUT facts read live from the running UPF (not from config)."""
        facts: dict[str, Any] = {"upf": "sd-core BESS-UPF",
                                 "pod": f"{self.namespace}/{self.pod}"}

        # bessd container image (authoritative version) via the k8s API.
        img = self._bessd_image()
        if img:
            facts["upf_image"] = img

        # mode / interfaces / pool / capacity from the mounted upf.jsonc.
        conf = self._read_upf_jsonc()
        if conf:
            facts["mode"] = conf.get("mode", self.cfg.mode or "unknown")
            facts["n3_iface"] = (conf.get("access") or {}).get("ifname", self.n3_port)
            facts["n6_iface"] = (conf.get("core") or {}).get("ifname", self.n6_port)
            if "ue_ip_pool" in (conf.get("cpiface") or {}):
                facts["ue_ip_pool"] = conf["cpiface"]["ue_ip_pool"]
            if "max_sessions" in conf:
                facts["max_sessions"] = conf["max_sessions"]
            if "workers" in conf:
                facts["workers"] = conf["workers"]
        else:
            facts["mode"] = self.cfg.mode or "unknown"
            facts["n3_iface"] = self.n3_port
            facts["n6_iface"] = self.n6_port

        # port drivers / link / speed from bessctl.
        ports = self._show_ports()
        facts["ports"] = {name: {k: p[k] for k in ("driver", "link", "speed") if k in p}
                          for name, p in ports.items()}
        return facts

    def _bessd_image(self) -> str:
        cmd = [*self._kubectl_base(), "get", "pod", self.pod, "-n", self.namespace,
               "-o", f"jsonpath={{.spec.containers[?(@.name=='{self.container}')].image}}"]
        self.store.record_command(" ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def _read_upf_jsonc(self) -> dict[str, Any]:
        try:
            raw = self._exec("cat", _UPF_JSONC)
        except RuntimeError:
            return {}
        return _parse_jsonc(raw)

    # --- counters -> measurement plane ----------------------------------------
    def port_counters(self) -> dict[str, dict[str, int]]:
        """Parse ``bessctl show port`` into per-port rx/tx packet/byte/drop counts."""
        return {name: {k: v for k, v in p.items() if isinstance(v, int)}
                for name, p in self._show_ports().items()}

    def _show_ports(self) -> dict[str, dict[str, Any]]:
        return _parse_show_port(self._bessctl("show", "port"))

    # --- tuning introspection -------------------------------------------------
    def worker_cores(self) -> list[int]:
        """CPU cores the bessd workers are pinned to (from ``bessctl show worker``).

        Used by the framework to keep co-located load (e.g. the traffic generator)
        OFF these cores — af_packet RX throughput is very sensitive to contention
        on the worker core.
        """
        cores = []
        for line in self._bessctl("show", "worker").splitlines():
            m = re.match(r"\s*(\d+)\s+\S+\s+(\d+)\s+\d+", line)
            if m:
                cores.append(int(m.group(2)))
        return cores

    # --- latency probe (TC-03): insert Timestamp+Measure across pktParse->executeFAR -
    # The built-in *_measure modules aren't on the wildcard-forwarded path, so (like
    # HANDOFF §7c) we splice our own Timestamp+Measure into the uplink segment, read
    # latency/jitter percentiles, then restore the pipeline exactly as found.
    def _pybess(self, body: str, label: str) -> str:
        header = (f"import sys, json\nsys.path.insert(0, {self.bess_path!r})\n"
                  f"from pybess.bess import BESS\n"
                  f"bess = BESS(); bess.connect(grpc_url={self.grpc!r})\n")
        script = header + body
        cmd = [*self._kubectl_base(), "exec", "-i", "-n", self.namespace, self.pod,
               "-c", self.container, "--", "python3", "-"]
        self._script_n += 1
        (self.store.raw / f"pybess_lat_{self._script_n:02d}_{label}.py").write_text(script)
        self.store.record_command(
            f"{' '.join(cmd)}  < raw/pybess_lat_{self._script_n:02d}_{label}.py")
        proc = subprocess.run(cmd, input=script, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"pybess '{label}' failed ({proc.returncode}):\n"
                               f"{proc.stderr.strip()}")
        return proc.stdout

    def latency_probe_install(self) -> None:
        body = (
            'def tgt(mod, og):\n'
            '    for g in bess.get_module_info(mod).ogates:\n'
            '        if g.ogate == og: return [g.name, g.igate]\n'
            'pp = tgt("pktParse", 1); ef = tgt("executeFAR", 1)\n'
            'bess.pause_all()\n'
            'try:\n'
            '    bess.create_module("Timestamp","ubench_ts",arg={"attr_name":"ubench_lat"})\n'
            '    bess.create_module("Measure","ubench_meas",arg={"attr_name":"ubench_lat"})\n'
            '    bess.disconnect_modules("pktParse",1)\n'
            '    bess.connect_modules("pktParse","ubench_ts",ogate=1,igate=0)\n'
            '    bess.connect_modules("ubench_ts",pp[0],ogate=0,igate=pp[1])\n'
            '    bess.disconnect_modules("executeFAR",1)\n'
            '    bess.connect_modules("executeFAR","ubench_meas",ogate=1,igate=0)\n'
            '    bess.connect_modules("ubench_meas",ef[0],ogate=0,igate=ef[1])\n'
            'finally:\n'
            '    bess.resume_all()\n'
            'print("RESTORE " + json.dumps({"pp": pp, "ef": ef}))\n')
        out = self._pybess(body, "probe_install")
        m = re.search(r"RESTORE (\{.*\})", out)
        self._lat_restore = json.loads(m.group(1)) if m else \
            {"pp": ["pdrLookup", 0], "ef": ["postULQosFlowMeasure", 0]}

    def latency_read(self, latency_percentiles, jitter_percentiles=(),
                     clear: bool = False) -> dict[str, Any]:
        lp = [float(p) for p in latency_percentiles]
        jp = [float(p) for p in jitter_percentiles]
        body = (
            f'r = bess.run_module_command("ubench_meas","get_summary",'
            f'"MeasureCommandGetSummaryArg",'
            f'{{"clear":{bool(clear)},"latency_percentiles":{lp},"jitter_percentiles":{jp}}})\n'
            'L = r.latency\n'
            'print("LAT " + json.dumps({"packets":r.packets,"min_ns":L.min_ns,'
            '"avg_ns":L.avg_ns,"max_ns":L.max_ns,"pct_ns":list(L.percentile_values_ns),'
            '"jitter_avg_ns":r.jitter.avg_ns,"jitter_pct_ns":list(r.jitter.percentile_values_ns)}))\n')
        out = self._pybess(body, "probe_read")
        return json.loads(re.search(r"LAT (\{.*\})", out).group(1))

    def latency_probe_remove(self) -> None:
        r = self._lat_restore or {"pp": ["pdrLookup", 0], "ef": ["postULQosFlowMeasure", 0]}
        body = (
            f'pp = {r["pp"]}; ef = {r["ef"]}\n'
            'bess.pause_all()\n'
            'try:\n'
            '    bess.disconnect_modules("pktParse",1)\n'
            '    bess.disconnect_modules("executeFAR",1)\n'
            '    bess.connect_modules("pktParse",pp[0],ogate=1,igate=pp[1])\n'
            '    bess.connect_modules("executeFAR",ef[0],ogate=1,igate=ef[1])\n'
            '    for m in ("ubench_ts","ubench_meas"):\n'
            '        try: bess.destroy_module(m)\n'
            '        except Exception: pass\n'
            'finally:\n'
            '    bess.resume_all()\n'
            'print("REMOVED")\n')
        self._pybess(body, "probe_remove")
        self._lat_restore = None

    # --- egress short-circuit -------------------------------------------------
    # Rewire executeFAR:1 -> [sink-MAC Update] -> coreQSplit so packets that a FAR has
    # decided to FORWARD reach the core-TX counter, bypassing coreRoutes/IPLookup (which
    # sink synthetic test traffic that has no real N6 route) and breaking the NIC VEB
    # re-circulation loop (egress dst-MAC -> a MAC on no VF). Pipeline-level, NOT per-rule:
    # used by suites whose *control* installs real FARs (e.g. pfcpsim per-UE sessions) so
    # their forwarded traffic egresses for a throughput measurement. Pairs with remove().
    def egress_shortcircuit_install(self) -> None:
        body = (
            'def tgt(mod, og):\n'
            '    for g in bess.get_module_info(mod).ogates:\n'
            '        if g.ogate == og: return [g.name, g.igate]\n'
            '    return None\n'
            'def feeder(dm, di):\n'
            '    for m in bess.list_modules().modules:\n'
            '        for g in bess.get_module_info(m.name).ogates:\n'
            '            if g.name == dm and g.igate == di: return [m.name, g.ogate]\n'
            '    return None\n'
            'ef = tgt("executeFAR", 1); cq = feeder("coreQSplit", 0)\n'
            'bess.pause_all()\n'
            'try:\n'
            '    try: bess.destroy_module("ubench_sink")\n'
            '    except Exception: pass\n'
            '    bess.create_module("Update","ubench_sink",'
            'arg={"fields":[{"offset":0,"size":6,"value":0x020000000099}]})\n'
            '    bess.disconnect_modules("executeFAR",1)\n'
            '    if cq: bess.disconnect_modules(cq[0],cq[1])\n'
            '    bess.connect_modules("executeFAR","ubench_sink",ogate=1,igate=0)\n'
            '    bess.connect_modules("ubench_sink","coreQSplit",ogate=0,igate=0)\n'
            'finally:\n'
            '    bess.resume_all()\n'
            'print("SC " + json.dumps({"ef": ef, "cq": cq}))\n')
        out = self._pybess(body, "sc_install")
        m = re.search(r"SC (\{.*\})", out)
        self._sc_restore = json.loads(m.group(1)) if m else None

    def egress_shortcircuit_remove(self) -> None:
        r = self._sc_restore or {"ef": None, "cq": None}
        body = (
            f'ef = {r.get("ef")}; cq = {r.get("cq")}\n'
            'bess.pause_all()\n'
            'try:\n'
            '    try: bess.disconnect_modules("executeFAR",1)\n'
            '    except Exception: pass\n'
            '    try: bess.destroy_module("ubench_sink")\n'
            '    except Exception: pass\n'
            '    if cq: bess.connect_modules(cq[0],"coreQSplit",ogate=cq[1],igate=0)\n'
            '    if ef: bess.connect_modules("executeFAR",ef[0],ogate=1,igate=ef[1])\n'
            'finally:\n'
            '    bess.resume_all()\n'
            'print("SC_REMOVED")\n')
        self._pybess(body, "sc_remove")
        self._sc_restore = None

    # --- downlink datapath (TC-02 bidirectional) ------------------------------
    # Empirically (probe), an injected downlink plain-IP packet (N6->UE) DOES traverse the
    # full DL fast-path — coreMetadata(src_iface=Core) -> pktParse -> pdrLookup -> QER ->
    # farLookup -> executeFAR — but lands on executeFAR gate 2 (farDrop), because there is
    # no real forwarding DL FAR for our synthetic UE on this testbed. So we short-circuit the
    # DROP gate to the access egress: the packet is counted out N3 after the entire DL
    # ingress+lookup+QER pipeline has run on it (the per-packet cost we want to measure),
    # mirroring the UL egress short-circuit. `dl_gate` lets a caller target a different
    # executeFAR gate (e.g. 0=accessRoutes) if a real DL FAR is later wired.
    def downlink_enable(self, dl_gate: int = 2) -> None:
        body = (
            'def tgt(mod, og):\n'
            '    for g in bess.get_module_info(mod).ogates:\n'
            '        if g.ogate == og: return [g.name, g.igate]\n'
            '    return None\n'
            'def feeder(dm, di):\n'
            '    for m in bess.list_modules().modules:\n'
            '        for g in bess.get_module_info(m.name).ogates:\n'
            '            if g.name == dm and g.igate == di: return [m.name, g.ogate]\n'
            '    return None\n'
            f'G = {int(dl_gate)}\n'
            'efG = tgt("executeFAR", G); aq = feeder("accessQSplit", 0)\n'
            'bess.pause_all()\n'
            'try:\n'
            '    try: bess.destroy_module("ubench_sink_dl")\n'
            '    except Exception: pass\n'
            '    bess.create_module("Update","ubench_sink_dl",'
            'arg={"fields":[{"offset":0,"size":6,"value":0x020000000098}]})\n'
            '    bess.disconnect_modules("executeFAR",G)\n'
            '    if aq: bess.disconnect_modules(aq[0],aq[1])\n'
            '    bess.connect_modules("executeFAR","ubench_sink_dl",ogate=G,igate=0)\n'
            '    bess.connect_modules("ubench_sink_dl","accessQSplit",ogate=0,igate=0)\n'
            'finally:\n'
            '    bess.resume_all()\n'
            'print("DL " + json.dumps({"G": G, "efG": efG, "aq": aq}))\n')
        out = self._pybess(body, "dl_enable")
        m = re.search(r"DL (\{.*\})", out)
        self._dl_restore = json.loads(m.group(1)) if m else None

    def downlink_disable(self) -> None:
        r = getattr(self, "_dl_restore", None) or {"G": 2, "efG": None, "aq": None}
        body = (
            f'G = {r.get("G", 2)}; efG = {r.get("efG")}; aq = {r.get("aq")}\n'
            'bess.pause_all()\n'
            'try:\n'
            '    try: bess.disconnect_modules("executeFAR",G)\n'
            '    except Exception: pass\n'
            '    try: bess.destroy_module("ubench_sink_dl")\n'
            '    except Exception: pass\n'
            '    if aq: bess.connect_modules(aq[0],"accessQSplit",ogate=aq[1],igate=0)\n'
            '    if efG: bess.connect_modules("executeFAR",efG[0],ogate=G,igate=efG[1])\n'
            'finally:\n'
            '    bess.resume_all()\n'
            'print("DL_REMOVED")\n')
        self._pybess(body, "dl_disable")
        self._dl_restore = None

    # --- liveness (for the N3 negative/robustness suite) ----------------------
    def healthy(self) -> bool:
        """True if bessd is responsive — i.e. it did NOT crash. The N3 negative suite
        sends malformed/edge-case GTP-U and asserts the UPF stays up."""
        try:
            self._bessctl("show", "worker")
            return True
        except Exception:
            return False

    def restart_count(self) -> int:
        """k8s restartCount of the bessd container. A malformed packet that crashes the
        BESS datapath makes this increment (k8s recreates the container) — that's how the
        N3 negative suite detects a data-plane crash even when bessd recovers."""
        cmd = [*self._kubectl_base(), "get", "pod", self.pod, "-n", self.namespace,
               "-o", f"jsonpath={{.status.containerStatuses[?(@.name=='{self.container}')]"
                     ".restartCount}"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return int(proc.stdout.strip() or "0")
        except ValueError:
            return 0

    def crash_reason(self) -> str:
        """One-line last-terminated reason for bessd (reason/exit/signal), for the crash
        report. Empty if it never terminated abnormally or k8s hasn't populated lastState
        yet (call after wait_healthy, once the new container is up)."""
        cmd = [*self._kubectl_base(), "get", "pod", self.pod, "-n", self.namespace, "-o", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return ""
        try:
            pod = json.loads(proc.stdout)
        except ValueError:
            return ""
        for c in pod.get("status", {}).get("containerStatuses", []):
            if c.get("name") != self.container:
                continue
            term = (c.get("lastState") or {}).get("terminated")
            if not term:
                return ""
            bits = [str(term.get("reason", "")).strip()]
            if term.get("exitCode") is not None:
                bits.append(f"exit={term['exitCode']}")
            if term.get("signal"):
                bits.append(f"signal={term['signal']}")
            return " ".join(b for b in bits if b)
        return ""

    def wait_healthy(self, timeout: int = 120) -> bool:
        """Block until bessd is back and responsive (container recreated after a crash,
        ports up). Returns False on timeout. The negative suite calls this after a detected
        crash so the next test doesn't hit a half-restarted container."""
        base = self._kubectl_base()
        subprocess.run([*base, "wait", "--for=condition=ready", f"pod/{self.pod}",
                        "-n", self.namespace, f"--timeout={timeout}s"],
                       capture_output=True, text=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.healthy():
                try:
                    self._show_ports()      # ports parsed -> datapath really up
                    time.sleep(3)
                    return True
                except Exception:
                    pass
            time.sleep(2)
        return False


# --- pure parsers (no I/O; unit-testable) -------------------------------------
def _parse_show_port(text: str) -> dict[str, dict[str, Any]]:
    """Parse ``bessctl show port`` output into ``{port: {driver, link, speed,
    rx_pkts, rx_bytes, rx_drops, tx_pkts, tx_bytes, tx_drops}}``."""
    ports: dict[str, dict[str, Any]] = {}
    cur: dict[str, Any] | None = None
    direction: str | None = None          # "rx" while in Inc/RX block, "tx" in Out/TX

    for line in text.splitlines():
        m = _PORT_HDR.match(line)
        if m:
            name, driver, _mac = m.groups()
            cur = {"driver": driver}
            ports[name] = cur
            direction = None
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("Speed"):
            sp = re.search(r"Speed\s+(\S+)", s)
            lk = re.search(r"Link\s+(\S+)", s)
            if sp:
                cur["speed"] = sp.group(1)
            if lk:
                cur["link"] = lk.group(1)
        elif s.startswith("Inc/RX"):
            direction = "rx"
            _grab_counts(s, cur, direction)
        elif s.startswith("Out/TX"):
            direction = "tx"
            _grab_counts(s, cur, direction)
        elif s.startswith("dropped:") and direction:
            _grab_counts(s, cur, direction)
    return ports


def _grab_counts(line: str, cur: dict[str, Any], direction: str) -> None:
    """Pull packets/bytes/dropped numbers off a counter line into cur[<dir>_<field>]."""
    field_map = {"packets": "pkts", "bytes": "bytes", "dropped": "drops"}
    for key, val in _NUM.findall(line):
        if key in field_map:
            cur[f"{direction}_{field_map[key]}"] = int(val.replace(",", ""))


def _parse_jsonc(raw: str) -> dict[str, Any]:
    """Parse JSON that may contain // or /* */ comments (upf.jsonc)."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    no_block = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    no_line = re.sub(r"//[^\n]*", "", no_block)
    try:
        return json.loads(no_line)
    except json.JSONDecodeError:
        return {}
