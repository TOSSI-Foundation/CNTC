"""Host-side GTP-U traffic generator for the af_packet path (Option A / Option 2).

This VM has no spare DPDK NIC, so instead of external testpmd we inject real
GTP-U uplink packets from the host's access macvlan into the UPF pod's ``access``
interface — which is the UPF's genuine af_packet RX socket (the N3 ingress).
Frames are crafted with scapy and replayed by ``tcpreplay`` at a controlled
packet rate. We only *send* here and report what was sent; the throughput/loss
math is done by the test case from the UPF's own port counters (the adapter),
so "loss" is measured against offered traffic — capturing both af_packet RX
drops and pipeline drops, which is the honest black-box result.

Requires scapy (craft) + tcpreplay (replay) on the host, and passwordless sudo
for the raw send. Config knobs (campaign YAML ``upf.extra``):

    gen_iface:     access            # host send interface (macvlan on eth0)
    n3_remote_ip:  192.168.252.3     # UPF access/N3 IP (outer dst)
    n3_remote_mac: ""                # UPF access MAC; "" = auto-resolve via ARP
    gnb_ip:        192.168.252.10    # outer src (emulated gNB)
    ue_ip:         192.168.100.5     # inner src (UE in the pool)
    inner_dst_ip:  8.8.8.8           # inner dst (anything routable to N6)
    teid:          100               # GTP-U TEID (wildcard PDR matches any)
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from upfbench.traffic.base import TrafficGenerator, Trial

# Outer Eth(14)+IP(20)+UDP(8)+GTPU(8) + inner IP(20)+UDP(8) = 78B of headers.
_GTPU_HDR_BYTES = 78
_FCS = 4            # NIC appends the 4B Ethernet FCS; RFC frame sizes are wire-incl-FCS.
_MAX_SCAPY = 1514   # largest frame we can craft (1500B MTU IP + 14B Ethernet header)
_PCAP_PKTS = 1000   # packets per pcap; tcpreplay loops it to reach the target count


class Generator(TrafficGenerator):
    name = "tcpreplay"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.extra
        self.iface = e.get("gen_iface", "access")
        self.remote_ip = e.get("n3_remote_ip", "192.168.252.3")
        self.remote_mac = e.get("n3_remote_mac", "")
        self.gnb_ip = e.get("gnb_ip", "192.168.252.10")
        self.ue_ip = e.get("ue_ip", "192.168.100.5")
        self.inner_dst = e.get("inner_dst_ip", "8.8.8.8")
        self.teid = int(e.get("teid", 100))
        # kubectl knobs (shared with the adapter) to read the UPF access MAC authoritatively.
        self.namespace = e.get("namespace", "aether-5gc")
        self.pod = e.get("pod", "upf-0")
        self.container = e.get("bessd_container", "bessd")
        self.kubectl = e.get("kubectl", "kubectl")
        self.kubeconfig = e.get("kubeconfig", "")
        self.n3_ifname = e.get("n3_ifname", "access")   # iface name *inside* the pod
        # How to learn the UPF N3 MAC when n3_remote_mac is blank: "pod" reads it
        # from inside a k8s pod (SD-Core's macvlan needs the authoritative MAC);
        # "arp" resolves it from the host neighbour table (Docker bridge UPFs like
        # OAI, where normal host ARP is correct).
        self.mac_via = str(e.get("n3_mac_via", "pod")).lower()
        # CPU affinity for the sender: "auto" = all host cores EXCEPT the bessd worker
        # cores (avoid starving the UPF's af_packet RX); an explicit list like "2-11";
        # or "none" to leave it unpinned. Proven ~3x effect on this VM.
        self.cpu_affinity = str(e.get("gen_cpu_affinity", "auto"))
        self._affinity: str | None = None   # resolved taskset spec (cached)
        self._pcaps: dict[int, str] = {}   # frame_size -> pcap path (cached)
        self.burst_pps = int(e.get('burst_pps', 5000))   # n3neg send_burst rate (af_packet)
        self._dir = Path(tempfile.mkdtemp(prefix="upfbench-pcap-"))

    # --- TrafficGenerator contract -------------------------------------------
    def run_trial(self, *, frame_size: int, offered_mpps: float, duration_s: int = 10,
                  teids: list[int] | None = None, ue_ips: list[str] | None = None,
                  encapsulation: str = "gtpu") -> Trial:
        pps = max(1, int(round(offered_mpps * 1e6)))
        pcap, actual_frame = self._pcap_for(frame_size, teids, ue_ips)
        loops = max(1, int(round(pps * duration_s / _PCAP_PKTS)))
        sent, secs = self._replay(pcap, pps, loops)
        offered = (sent / secs / 1e6) if secs > 0 else 0.0
        gbps = offered * actual_frame * 8 / 1e3   # Mpps * bytes * 8 -> Mbps -> /1e3 Gbps
        # rx/tx are filled by the test case from UPF counters; left 0 here.
        return Trial(offered_mpps=round(offered, 4), rx_mpps=0.0, tx_mpps=0.0,
                     gbps=round(gbps, 4), sent_pkts=sent, duration_s=round(secs, 3))

    def send_burst(self, pkt_bytes, count: int, pps: int = 0) -> int:
        """af_packet analog of TRex.send_burst, used by the N3 robustness suite (n3neg).

        Inject a RAW packet (already-serialized Ethernet bytes) ``count`` times via
        tcpreplay on the host access interface. The bytes are written to the pcap VERBATIM
        (no scapy re-parse) so malformed / truncated frames survive exactly as crafted. The
        caller bakes the UPF access MAC into the frame's dst (upf.extra.n3_remote_mac) so the
        frame lands on the UPF's access macvlan — the same delivery path the perf suite uses.
        Returns the number of packets sent."""
        import struct
        pkt = bytes(pkt_bytes)
        pcap = str(self._dir / f"burst_{len(pkt)}.pcap")
        with open(pcap, "wb") as f:
            # pcap global header: magic a1b2c3d4, ver 2.4, tz/sig 0, snaplen 65535, DLT=EN10MB(1)
            f.write(struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))
            # a single packet record (ts 0); tcpreplay --loop replays it `count` times
            f.write(struct.pack("<IIII", 0, 0, len(pkt), len(pkt)))
            f.write(pkt)
        pps = int(pps) or int(getattr(self, 'burst_pps', 0)) or 5000  # af_packet ingress ceiling
        sent, _ = self._replay(pcap, pps=int(pps), loops=max(1, int(count)))
        return int(sent) if sent else max(1, int(count))

    # --- packet crafting ------------------------------------------------------
    def _pcap_for(self, frame_size: int, teids: list[int] | None = None,
                  ue_ips: list[str] | None = None) -> tuple[str, int]:
        # One flow per TEID. If ue_ips is given (Suite 2), each flow uses the matching
        # UE IP so it hits that session's PDR; otherwise UE IPs are derived from the
        # default. teids=None -> a single flow on the default TEID.
        flow_teids = list(teids) if teids else [self.teid]
        key = (frame_size, tuple(flow_teids), tuple(ue_ips) if ue_ips else None)
        if key in self._pcaps:
            return self._pcaps[key], frame_size
        from scapy.contrib.gtp import GTP_U_Header
        from scapy.all import Ether, IP, UDP, wrpcap

        dst_mac = self.remote_mac or self._resolve_mac()
        # Wire frame includes the 4B FCS the NIC appends, so the crafted frame is
        # 4B smaller; cap at 1514 (1500B MTU + 14B Ethernet header).
        scapy_len = min(max(_GTPU_HDR_BYTES, frame_size - _FCS), _MAX_SCAPY)
        payload = scapy_len - _GTPU_HDR_BYTES
        base = int(self.ue_ip.rsplit(".", 1)[1])
        prefix = self.ue_ip.rsplit(".", 1)[0]
        flows = []
        for i, teid in enumerate(flow_teids):
            ue = ue_ips[i] if ue_ips else f"{prefix}.{(base + i) % 254 + 1}"
            inner = IP(src=ue, dst=self.inner_dst) / UDP(sport=1234, dport=80) / (b"\x00" * payload)
            flows.append(Ether(dst=dst_mac) / IP(src=self.gnb_ip, dst=self.remote_ip) /
                         UDP(sport=2152, dport=2152) / GTP_U_Header(teid=int(teid)) / inner)
        # tile the flow set up to _PCAP_PKTS so tcpreplay cycles through all flows
        reps = max(1, _PCAP_PKTS // len(flows))
        pkts = (flows * reps)[:max(_PCAP_PKTS, len(flows))]
        path = str(self._dir / f"gtpu_{frame_size}_{len(flows)}flows.pcap")
        wrpcap(path, pkts)
        wire = len(flows[0]) + _FCS
        self.store.record_command(
            f"# crafted {len(pkts)}x GTP-U uplink frames ({wire}B wire, {len(flows)} flow(s)) -> {path}")
        self._pcaps[key] = path
        return path, wire

    def _resolve_mac(self) -> str:
        """Learn the UPF N3 MAC. Docker-bridge UPFs (n3_mac_via=arp) resolve via the
        host neighbour table; k8s pods read it authoritatively from inside the pod."""
        if self.mac_via == "arp":
            return self._resolve_mac_arp()
        return self._resolve_mac_pod()

    def _resolve_mac_arp(self) -> str:
        """Resolve the N3 MAC from the host ARP/neighbour table (a ping primes it).
        Correct for normal bridges (e.g. OAI on the demo-oai bridge)."""
        subprocess.run(["ping", "-c", "1", "-W", "1", self.remote_ip], capture_output=True)
        out = subprocess.run(["ip", "neigh", "show", self.remote_ip],
                             capture_output=True, text=True).stdout
        m = re.search(r"lladdr\s+([0-9a-f:]{17})", out)
        if not m:
            raise RuntimeError(f"could not ARP-resolve {self.remote_ip}; "
                               f"set upf.extra.n3_remote_mac explicitly")
        return m.group(1)

    def _resolve_mac_pod(self) -> str:
        """Read the UPF access interface MAC straight from the pod (authoritative).

        Host ARP can return the wrong MAC here — other macvlans on the same parent
        answer for the N3 IP — so we read /sys/class/net/<iface>/address in the pod.
        """
        cmd = [self.kubectl]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        cmd += ["exec", "-n", self.namespace, self.pod, "-c", self.container, "--",
                "cat", f"/sys/class/net/{self.n3_ifname}/address"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        mac = proc.stdout.strip()
        if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f:]{17}", mac):
            raise RuntimeError(
                f"could not read UPF {self.n3_ifname} MAC from pod "
                f"({proc.returncode}): {proc.stdout!r} {proc.stderr!r}; "
                f"set upf.extra.n3_remote_mac explicitly")
        return mac

    # --- replay ---------------------------------------------------------------
    def _replay(self, pcap: str, pps: int, loops: int) -> tuple[int, float]:
        cmd = ["sudo"]
        aff = self._affinity_spec()
        if aff:
            cmd += ["taskset", "-c", aff]
        cmd += ["tcpreplay", "-i", self.iface, "-K", f"--pps={pps}", f"--loop={loops}", pcap]
        self.store.record_command(" ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        text = proc.stdout + proc.stderr
        if proc.returncode != 0:
            raise RuntimeError(f"tcpreplay failed ({proc.returncode}):\n{text.strip()}")
        return _parse_tcpreplay(text)

    def _affinity_spec(self) -> str:
        """Resolve the taskset core spec once. 'auto' avoids the UPF worker cores."""
        if self._affinity is not None:
            return self._affinity
        spec = self.cpu_affinity.strip().lower()
        if spec in ("", "none"):
            self._affinity = ""
        elif spec == "auto":
            self._affinity = self._auto_affinity()
        else:
            self._affinity = self.cpu_affinity
        if self._affinity:
            self.store.record_command(f"# generator CPU affinity: taskset -c {self._affinity}")
        return self._affinity

    def _auto_affinity(self) -> str:
        """All host cores except the UPF's bessd worker cores (read from the pod)."""
        try:
            out = self._kubectl_text("bessctl", "show", "worker")
            workers = {int(m.group(2)) for m in
                       re.finditer(r"^\s*(\d+)\s+\S+\s+(\d+)\s+\d+", out, re.M)}
        except Exception:
            return ""   # detection failed -> leave unpinned
        ncpu = os.cpu_count() or 1
        free = [c for c in range(ncpu) if c not in workers]
        return ",".join(map(str, free)) if (workers and free) else ""

    def _kubectl_text(self, *argv: str) -> str:
        cmd = [self.kubectl]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        cmd += ["exec", "-n", self.namespace, self.pod, "-c", self.container, "--", *argv]
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def _parse_tcpreplay(text: str) -> tuple[int, float]:
    """Pull (packets_sent, seconds) from tcpreplay's 'Actual: N packets ... in S seconds'."""
    pk = re.search(r"Actual:\s+([\d,]+)\s+packets", text)
    se = re.search(r"in\s+([\d.]+)\s+seconds", text)
    sent = int(pk.group(1).replace(",", "")) if pk else 0
    secs = float(se.group(1)) if se else 0.0
    return sent, secs
