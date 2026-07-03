"""free5GC UPF adapter (fourth UPF — gtp5g, like Open5GS).

free5GC's UPF (``free5gc-upfd``) runs as a Docker container (default ``upf``) and forwards
via the **gtp5g kernel module**: GTP-U arrives on the N3 netdev (``eth0``) and the
decapsulated inner packet is delivered into the kernel through the gtp5g TUN ``upfgtp``
(then routed/NAT'd out N6). So, like the Open5GS and OAI adapters, this one shells into the
container with ``docker exec`` and reads facts + per-interface netdev counters
(``/proc/net/dev``). It is driven over N4 by the pfcpsim control and over N3 by tcpreplay —
the suites are unchanged.

Note: free5GC, unlike Open5GS, may program gtp5g for an *external* pfcpsim-driven session
(Open5GS only programs it for its own SMF). Whether the pfcpsim-driven data-plane suites
(1/2/4) forward is validated per-deployment; Suite 3 (PFCP conformance) works regardless.

Config knobs (campaign YAML ``upf.extra``, defaults shown)::

    container:     upf          # the free5GC UPF container name
    docker_cmd:    "sudo docker"
    n3_iface:      eth0         # container iface carrying N3 (GTP-U)
    n6_iface:      upfgtp       # gtp5g TUN where decapsulated UE/N6 traffic appears
    n6_fwd_field:  rx_pkts      # which n6 counter == uplink-forwarded (see fwd_field())
"""
from __future__ import annotations

import subprocess
import time
from typing import Any

from upfbench.adapters.base import UPFAdapter


class Adapter(UPFAdapter):
    name = "free5gc_upf"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.extra
        self.container = e.get("container", "upf")
        self.docker = e.get("docker_cmd", "sudo docker").split()
        self.n3_iface = cfg.n3_iface or "eth0"
        self.n6_iface = cfg.n6_iface or "upfgtp"
        # Which N6 counter reflects uplink-forwarded packets. upfgtp is the gtp5g TUN: the
        # decapsulated inner packet is delivered into the kernel and counts as rx_pkts on
        # upfgtp (tx stays flat) — same pattern as Open5GS ogstun / OAI tun0. Configurable
        # in case a deployment routes the uplink differently; verified live at bring-up.
        self._fwd = e.get("n6_fwd_field", "rx_pkts")

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

    # --- reset -> fresh UPF session/datapath state ----------------------------
    def reset(self) -> None:
        """Restart the free5GC UPF container for a clean session/datapath state."""
        cmd = [*self.docker, "restart", self.container]
        self.store.record_command(" ".join(cmd))
        subprocess.run(cmd, capture_output=True, text=True)
        for _ in range(30):
            time.sleep(1)
            h = self._inspect("{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}")
            if h in ("healthy", "none"):
                break
        time.sleep(5)

    # --- introspection -> report SUT section ----------------------------------
    def describe(self) -> dict[str, Any]:
        facts: dict[str, Any] = {"upf": "free5GC-UPF", "container": self.container}
        img = self._inspect("{{.Config.Image}}")
        if img:
            facts["upf_image"] = img
        facts["mode"] = "gtp5g"      # free5GC 5G UPF data path = gtp5g kernel module
        facts["n3_iface"] = self.n3_iface
        facts["n6_iface"] = self.n6_iface
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

    # --- counters -> measurement plane ----------------------------------------
    def port_counters(self) -> dict[str, dict[str, int]]:
        """Per-interface counters from /proc/net/dev (free5GC forwards via Linux ifaces)."""
        return _parse_proc_net_dev(self._exec("cat", "/proc/net/dev"))

    def fwd_field(self) -> str:
        # upfgtp is the gtp5g TUN: the decapsulated uplink packet is delivered into the
        # kernel and counts as rx_pkts on upfgtp (tx stays flat).
        return self._fwd


def _parse_proc_net_dev(text: str) -> dict[str, dict[str, int]]:
    """Parse /proc/net/dev into {iface: {rx_pkts, rx_bytes, rx_drops, tx_pkts,
    tx_bytes, tx_drops}}."""
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
