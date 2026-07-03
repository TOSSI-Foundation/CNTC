"""Cisco TRex GTP-U generator for kernel-bypass UPFs (DPDK / AF_XDP / CNDP).

Why this exists: in these modes the UPF's N3 access port is a DPDK/XDP-owned VF
*inside* the pod — there is no host kernel socket to inject into (so ``tcpreplay``
can't reach it), and the UPF forwards millions of pps (far above tcpreplay's ~0.06
Mpps ceiling). So we drive it the way the paper testbed does: TRex on a spare SR-IOV
VF on the *same PF* as the UPF's access VF; frames whose dst MAC = the UPF access
VF's MAC are hairpinned by the NIC's internal switch (VEB) straight into the UPF.
Validated: 20k sent on the gen VF == 20k counted at the UPF accessFast RX.

We only *send* here and report what was sent (offered pps); the throughput/loss math
is done by the test case from the UPF's own port counters (the adapter) — same black-box
contract as the tcpreplay generator, so results are directly comparable across modes.

Config knobs (campaign YAML ``upf.extra``, defaults shown)::

    trex_root:     /home/three/trex-v3.08        # TRex install (has t-rex-64 + python API)
    trex_cfg:      configs/trex_cfg.yaml          # pinned to the gen VFs only (no broad probe)
    trex_server:   127.0.0.1                      # TRex stateless server address
    trex_tx_port:  0                              # port index that hairpins to the UPF access VF
    n3_remote_mac: "00:11:22:33:44:33"            # UPF access VF MAC (uplink dst); VEB target
    gnb_ip:        192.168.252.10                 # outer src (emulated gNB)
    n3_remote_ip:  192.168.252.3                  # outer dst (UPF access/N3 IP)
    ue_ip:         192.168.100.5                  # inner src (UE in the pool)
    inner_dst_ip:  8.8.8.8                        # inner dst (routable to N6)
    teid:          100                            # default GTP-U TEID

The generator manages the TRex server: it connects to an already-running one, or
starts ``t-rex-64 -i --cfg <trex_cfg>`` itself and stops it at process exit.
"""
from __future__ import annotations

import atexit
import os
import subprocess
import sys
import time
from pathlib import Path

from upfbench.traffic.base import TrafficGenerator, Trial

# Same header/frame accounting as the tcpreplay generator, so frame sizes are comparable.
_GTPU_HDR_BYTES = 78      # Eth14 + IP20 + UDP8 + GTPU8 + innerIP20 + innerUDP8
_DL_HDR_BYTES = 42        # downlink plain frame: Eth14 + IP20 + UDP8 (UPF GTP-encaps on egress)
_FCS = 4                  # NIC appends the 4B Ethernet FCS; RFC frame sizes are wire-incl-FCS
_MAX_SCAPY = 1514


class Generator(TrafficGenerator):
    name = "trex"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.extra
        self.root = e.get("trex_root", "/home/three/trex-v3.08")
        cfgp = e.get("trex_cfg", "configs/trex_cfg.yaml")
        self.cfg_path = cfgp if os.path.isabs(cfgp) else str(Path.cwd() / cfgp)
        self.server = e.get("trex_server", "127.0.0.1")
        self.tx_port = int(e.get("trex_tx_port", 0))
        self.tx_ports = [int(x) for x in e.get("trex_tx_ports", [self.tx_port])]
        self.dst_mac = e.get("n3_remote_mac", "00:11:22:33:44:33")
        self.src_mac = e.get("trex_src_mac", "00:11:22:33:44:35")
        self.gnb_ip = e.get("gnb_ip", "192.168.252.10")
        self.remote_ip = e.get("n3_remote_ip", "192.168.252.3")
        self.ue_ip = e.get("ue_ip", "192.168.100.5")
        self.inner_dst = e.get("inner_dst_ip", "8.8.8.8")
        self.teid = int(e.get("teid", 100))
        # NIC RSS spread: number of distinct OUTER 5-tuples to emit so the i40e RSS hash
        # fans traffic across all RX queues/workers. A single flow hashes to one queue =>
        # one worker (the ~3 Mpps/core ceiling); the reference used txonly-multi-flow for
        # the same reason. Set >= worker count (more = evener spread).
        self.rss_flows = int(e.get("rss_flows", 64))
        # --- downlink (TC-02 bidirectional): a 2nd gen VF on the same PF as the UPF core VF
        # hairpins plain-IP frames (dst = UE-IP) into N6; the UPF GTP-encaps them out N3. ---
        self.dl_port = int(e.get("trex_dl_port", 1))
        self.dl_dst_mac = e.get("n6_remote_mac", "00:11:22:33:44:34")    # UPF core VF (N6) MAC
        self.dl_src_mac = e.get("trex_dl_src_mac", "00:11:22:33:44:36")  # gen VF on the core PF
        self.dl_inner_src = e.get("dl_inner_src_ip", "9.9.9.9")          # emulated internet host
        self._client = None
        self._server_proc: subprocess.Popen | None = None
        # make the TRex python automation API importable
        api = str(Path(self.root) / "automation/trex_control_plane/interactive")
        if api not in sys.path:
            sys.path.insert(0, api)

    # --- TrafficGenerator contract -------------------------------------------
    def run_trial(self, *, frame_size: int, offered_mpps: float, duration_s: int = 10,
                  teids: list[int] | None = None, ue_ips: list[str] | None = None,
                  encapsulation: str = "gtpu") -> Trial:
        from trex.stl.api import STLStream, STLPktBuilder, STLTXCont
        c = self._connect()
        flows = self._flows(teids, ue_ips)
        total_pps = max(1, int(round(offered_mpps * 1e6)))
        per_pps = max(1, total_pps // (len(flows) * len(self.tx_ports)))
        wire = 0
        streams = []
        for teid, ue, osrc in flows:
            pkt, wire = self._build_pkt(frame_size, teid, ue, osrc)
            streams.append(STLStream(packet=STLPktBuilder(pkt=pkt),
                                     mode=STLTXCont(pps=per_pps)))
        c.reset(ports=self.tx_ports)
        c.add_streams(streams, ports=self.tx_ports)
        c.clear_stats()
        self.store.record_command(
            f"# TRex: {len(streams)} GTP-U stream(s) @ {per_pps} pps each "
            f"({wire}B wire) on port {self.tx_port} for {duration_s}s")
        c.start(ports=self.tx_ports, mult="1", duration=duration_s, force=True)
        try:
            c.wait_on_traffic(ports=self.tx_ports, timeout=duration_s + 30)
        except Exception:
            # Under many back-to-back trials (e.g. the TC-01 binary search) TRex can miss
            # the auto-stop event and wait_on_traffic times out. Force-stop and use the
            # counters we have rather than failing the whole sweep.
            try:
                c.stop(ports=self.tx_ports)
            except Exception:
                pass
        _st = c.get_stats()
        sent = sum(int(_st[_p]["opackets"]) for _p in self.tx_ports)
        # Rate basis is the TRAFFIC on-time (TRex ran for exactly duration_s), NOT wall
        # clock — wall clock includes start/stop/sync overhead and would understate rates.
        secs = float(duration_s)
        offered = (sent / secs / 1e6) if secs > 0 else 0.0
        gbps = offered * wire * 8 / 1e3
        return Trial(offered_mpps=round(offered, 4), rx_mpps=0.0, tx_mpps=0.0,
                     gbps=round(gbps, 4), sent_pkts=sent, duration_s=round(secs, 3))

    def send_burst(self, pkt_bytes, count: int, pps: int = 50000) -> int:
        """Transmit a fixed burst of a RAW packet (bytes) and return the count sent. We
        take pre-serialized bytes (not a scapy object) so the caller's scapy and TRex's
        bundled scapy never clash. Used by the N3 negative/robustness suite to inject
        malformed / unknown-TEID / PSC GTP-U (the frame's dst MAC must be the UPF access
        VF MAC so the NIC VEB hairpins it into the UPF)."""
        from trex.stl.api import STLStream, STLPktBuilder, STLTXSingleBurst
        c = self._connect()
        c.reset(ports=self.tx_ports)
        c.add_streams([STLStream(packet=STLPktBuilder(pkt_buffer=bytes(pkt_bytes)),
                                 mode=STLTXSingleBurst(total_pkts=int(count), pps=pps))],
                      ports=[self.tx_port])
        c.clear_stats()
        c.start(ports=[self.tx_port], mult="1", duration=-1, force=True)
        try:
            c.wait_on_traffic(ports=[self.tx_port], timeout=30)
        except Exception:
            try:
                c.stop(ports=self.tx_ports)
            except Exception:
                pass
        return int(c.get_stats()[self.tx_port]["opackets"])

    def run_bidir(self, *, frame_size: int, ul_mpps: float, dl_mpps: float,
                  duration_s: int = 8, teids: list[int] | None = None,
                  ue_ips: list[str] | None = None) -> dict:
        """Drive UPLINK GTP-U (port 0 → access) and DOWNLINK plain-IP (port 1 → core)
        simultaneously, and return what was offered each way. The test case reads the UPF's
        own port counters to get forwarded UL (core TX) and DL (access TX)."""
        from trex.stl.api import STLStream, STLPktBuilder, STLTXCont
        c = self._connect()
        c.acquire(ports=[self.dl_port], force=True)

        ul_flows = self._flows(teids, ue_ips)
        ul_total = max(1, int(round(ul_mpps * 1e6))); ul_per = max(1, ul_total // len(ul_flows))
        ul_streams, ul_wire = [], 0
        for teid, ue, osrc in ul_flows:
            pkt, ul_wire = self._build_pkt(frame_size, teid, ue, osrc)
            ul_streams.append(STLStream(packet=STLPktBuilder(pkt=pkt), mode=STLTXCont(pps=ul_per)))

        dl_ues = ue_ips or [ue for _, ue, _ in ul_flows]
        dl_total = max(1, int(round(dl_mpps * 1e6))); dl_per = max(1, dl_total // max(1, len(dl_ues)))
        dl_streams, dl_wire = [], 0
        for i, ue in enumerate(dl_ues):
            pkt, dl_wire = self._build_dl_pkt(frame_size, ue, self._osrc(i))
            dl_streams.append(STLStream(packet=STLPktBuilder(pkt=pkt), mode=STLTXCont(pps=dl_per)))

        ports = [self.tx_port, self.dl_port]
        c.reset(ports=ports)
        c.add_streams(ul_streams, ports=[self.tx_port])
        c.add_streams(dl_streams, ports=[self.dl_port])
        c.clear_stats()
        self.store.record_command(
            f"# TRex bidir: UL {len(ul_streams)}strm@{ul_per}pps on p{self.tx_port} + "
            f"DL {len(dl_streams)}strm@{dl_per}pps on p{self.dl_port} for {duration_s}s")
        c.start(ports=ports, mult="1", duration=duration_s, force=True)
        try:
            c.wait_on_traffic(ports=ports, timeout=duration_s + 30)
        except Exception:
            try: c.stop(ports=ports)
            except Exception: pass
        st = c.get_stats()
        secs = float(duration_s)
        return {"ul_sent": int(st[self.tx_port]["opackets"]),
                "dl_sent": int(st[self.dl_port]["opackets"]),
                "ul_wire": ul_wire, "dl_wire": dl_wire, "secs": secs}

    # --- packet crafting ------------------------------------------------------
    @staticmethod
    def _osrc(i: int) -> str:
        """Distinct OUTER source IP per flow so the NIC RSS hash spreads RX queues."""
        return f"10.{(i // 65536) % 256}.{(i // 256) % 256}.{i % 256}"

    def _flows(self, teids, ue_ips):
        """Return [(teid, ue_ip, outer_src), ...]. Suite 2 passes parallel teids+ue_ips
        (one per session); otherwise one logical flow spread across ``rss_flows`` distinct
        outer 5-tuples so RSS engages every worker (single outer tuple => 1 queue/worker)."""
        if teids:
            base = int(self.ue_ip.rsplit(".", 1)[1])
            prefix = self.ue_ip.rsplit(".", 1)[0]
            out = []
            for i, teid in enumerate(teids):
                ue = ue_ips[i] if ue_ips else f"{prefix}.{(base + i) % 254 + 1}"
                out.append((int(teid), ue, self._osrc(i)))
            return out
        return [(self.teid, self.ue_ip, self._osrc(i))
                for i in range(max(1, self.rss_flows))]

    def _build_pkt(self, frame_size: int, teid: int, ue_ip: str, outer_src: str = None):
        from scapy.contrib.gtp import GTP_U_Header
        from scapy.layers.l2 import Ether
        from scapy.layers.inet import IP, UDP
        outer_src = outer_src or self.gnb_ip
        scapy_len = min(max(_GTPU_HDR_BYTES, frame_size - _FCS), _MAX_SCAPY)
        payload = scapy_len - _GTPU_HDR_BYTES
        inner = IP(src=ue_ip, dst=self.inner_dst) / UDP(sport=1234, dport=80) / (b"\x00" * payload)
        pkt = (Ether(dst=self.dst_mac, src=self.src_mac) /
               IP(src=outer_src, dst=self.remote_ip) /
               UDP(sport=2152, dport=2152) / GTP_U_Header(teid=int(teid)) / inner)
        return pkt, len(pkt) + _FCS

    def _build_dl_pkt(self, frame_size: int, ue_ip: str, outer_src: str = None):
        """A DOWNLINK frame: plain IP from the 'internet' to a UE-IP, dst MAC = the UPF core
        VF (so the VEB hairpins it into N6). The UPF matches the DL PDR and GTP-encaps it out
        N3. No GTP header here — the UPF adds it."""
        from scapy.layers.l2 import Ether
        from scapy.layers.inet import IP, UDP
        src = outer_src or self.dl_inner_src
        scapy_len = min(max(_DL_HDR_BYTES, frame_size - _FCS), _MAX_SCAPY)
        payload = scapy_len - _DL_HDR_BYTES
        pkt = (Ether(dst=self.dl_dst_mac, src=self.dl_src_mac) /
               IP(src=src, dst=ue_ip) / UDP(sport=1234, dport=80) / (b"\x00" * payload))
        return pkt, len(pkt) + _FCS

    # --- TRex server lifecycle ------------------------------------------------
    def _connect(self):
        if self._client is not None:
            return self._client
        from trex.stl.api import STLClient
        c = STLClient(server=self.server)
        try:
            c.connect()
        except Exception:
            self._start_server()
            for attempt in range(30):
                try:
                    c.connect()
                    break
                except Exception:
                    if attempt == 29:
                        raise
                    time.sleep(2)
        c.acquire(ports=[self.tx_port], force=True)
        self._client = c
        atexit.register(self._cleanup)
        return c

    def _start_server(self) -> None:
        # A SIGKILL'd TRex leaks its 1GiB hugepage files (free->0), which then starves the
        # UPF's bessd of hugepages on its next restart. Clean stale files and cap TRex's
        # hugepage footprint so the generator and the SUT coexist on the shared pool.
        subprocess.run("sudo find /dev/hugepages -maxdepth 1 -name 'rtemap_*' -delete",
                       shell=True, capture_output=True)
        # hugepage cap is set via 'limit_memory' in the cfg (not a CLI flag in v3.08)
        cmd = ["sudo", "./t-rex-64", "-i", "--cfg", self.cfg_path]
        self.store.record_command(f"(cd {self.root} && {' '.join(cmd)}) &")
        self._server_proc = subprocess.Popen(
            cmd, cwd=self.root,
            stdout=open("/tmp/upfbench_trex.log", "w"), stderr=subprocess.STDOUT)

    def _cleanup(self) -> None:
        try:
            if self._client is not None:
                self._client.stop(ports=[self.tx_port])
                self._client.disconnect()
        except Exception:
            pass
        # only stop the server if *we* started it (don't kill an externally-run one)
        if self._server_proc is not None:
            subprocess.run(["sudo", "pkill", "-f", "t-rex-64"], capture_output=True)
