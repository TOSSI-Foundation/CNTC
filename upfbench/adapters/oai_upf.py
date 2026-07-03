"""OAI-UPF adapter (second UPF — proves the framework is UPF-agnostic).

OAI-UPF runs as a Docker container (default ``oai-upf``) and forwards via Linux
interfaces (simpleswitch datapath), not BESS — so this adapter shells into the
container with ``docker exec`` and reads facts + per-interface netdev counters
(``/proc/net/dev``) instead of ``bessctl``. It is driven over N4 by the pfcpsim
control (with PFCPSIM_NO_URR, since OAI rejects URRs) and over N3 by tcpreplay,
exactly like the SD-Core adapter — the suites are unchanged.

Config knobs (campaign YAML ``upf.extra``, defaults shown)::

    container:   oai-upf            # the OAI-UPF container name
    docker_cmd:  "sudo docker"      # how to invoke docker (sudo unless in docker group)
    n3_iface:    eth0               # container iface carrying N3 (GTP-U)
    n6_iface:    tun0               # container iface carrying UE/N6 traffic
"""
from __future__ import annotations

import re
import subprocess
import time
from typing import Any

from upfbench.adapters.base import UPFAdapter


class Adapter(UPFAdapter):
    name = "oai_upf"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.extra
        self.container = e.get("container", "oai-upf")
        self.docker = e.get("docker_cmd", "sudo docker").split()
        self.n3_iface = cfg.n3_iface or "eth0"
        self.n6_iface = cfg.n6_iface or "tun0"

    # --- command plumbing -----------------------------------------------------
    def _exec(self, *argv: str) -> str:
        cmd = [*self.docker, "exec", self.container, *argv]
        self.store.record_command(" ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"docker exec failed ({proc.returncode}): {' '.join(argv)}\n"
                               f"{proc.stderr.strip()}")
        return proc.stdout

    def _inspect(self, fmt: str) -> str:
        cmd = [*self.docker, "inspect", "-f", fmt, self.container]
        self.store.record_command(" ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    # --- reset -> fresh OAI session state -------------------------------------
    def reset(self) -> None:
        """Restart the OAI-UPF container for a clean session table. OAI's batch
        session establishment wedges after the performance suite's churn (so a
        following load/pfcp suite can't establish); a restart clears it."""
        cmd = [*self.docker, "restart", self.container]
        self.store.record_command(" ".join(cmd))
        subprocess.run(cmd, capture_output=True, text=True)
        # wait for the container's healthcheck to go healthy (OAI needs a few s to
        # re-init the datapath); fall back to a fixed settle if it has no healthcheck.
        for _ in range(30):
            time.sleep(1)
            h = self._inspect("{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}")
            if h in ("healthy", "none"):
                break
        time.sleep(5)

    # --- introspection -> report SUT section ----------------------------------
    def describe(self) -> dict[str, Any]:
        facts: dict[str, Any] = {"upf": "OAI-UPF", "container": self.container}
        img = self._inspect("{{.Config.Image}}")
        if img:
            facts["upf_image"] = img
        facts["mode"] = self._datapath_mode()
        facts["n3_iface"] = self.n3_iface
        facts["n6_iface"] = self.n6_iface
        # interface addresses (UPF data plane)
        try:
            addrs = {}
            for line in self._exec("ip", "-br", "addr").splitlines():
                f = line.split()
                if f and f[0].split("@")[0] in (self.n3_iface, self.n6_iface):
                    addrs[f[0].split("@")[0]] = f[2] if len(f) > 2 else ""
            if addrs:
                facts["ifaces"] = addrs
        except RuntimeError:
            pass
        return facts

    def _datapath_mode(self) -> str:
        # read enable_bpf_datapath from the mounted config; default simpleswitch
        try:
            cfg = self._exec("cat", "/openair-upf/etc/config.yaml")
            m = re.search(r"enable_bpf_datapath:\s*(\w+)", cfg)
            if m and m.group(1).lower() in ("yes", "on", "true"):
                return "ebpf"
        except RuntimeError:
            pass
        return "simpleswitch"

    # --- counters -> measurement plane ----------------------------------------
    def port_counters(self) -> dict[str, dict[str, int]]:
        """Per-interface counters from /proc/net/dev (OAI forwards via Linux ifaces)."""
        return _parse_proc_net_dev(self._exec("cat", "/proc/net/dev"))

    def fwd_field(self) -> str:
        # OAI-UPF's N6 (tun0) is a TUN device: the UPF writes each decapsulated
        # uplink packet into the kernel, which the interface counts as rx_pkts
        # (tx_pkts stays flat). Verified live: a TEID-aligned uplink blast moved
        # tun0 rx_pkts, not tx_pkts.
        return "rx_pkts"


def _parse_proc_net_dev(text: str) -> dict[str, dict[str, int]]:
    """Parse /proc/net/dev into {iface: {rx_pkts, rx_bytes, rx_drops, tx_pkts,
    tx_bytes, tx_drops}}. Columns: rx[bytes packets errs drop ...] tx[bytes packets
    errs drop ...]."""
    out: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        cols = rest.split()
        if len(cols) < 16:
            continue
        out[name] = {
            "rx_bytes": int(cols[0]), "rx_pkts": int(cols[1]), "rx_drops": int(cols[3]),
            "tx_bytes": int(cols[8]), "tx_pkts": int(cols[9]), "tx_drops": int(cols[11]),
        }
    return out
