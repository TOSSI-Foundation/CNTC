"""Shared helpers for the eBPF/XDP dataplane assurance suite.

The tests read the adapter's ``bpf_introspect()`` (XDP attach state + BPF map contents) and,
for the binding tests, drive pfcpsim to install/delete a session and re-read the maps. Fast-path
tests inject GTP-U straight to the UPF's N3 address on UDP 2152: an eBPF/XDP UPF's program sits
on the netdev RX, so a plain datagram to ``n3_addr:2152`` reaches the XDP hook (verified — it
increments ``rx_gtp_pdu`` and the forward counter). No external generator / veth is needed, which
also makes injection robust to the eUPF pod IP changing across restarts.
"""
from __future__ import annotations

import socket

from upfbench.results import TestResult


def not_ebpf(ctx) -> bool:
    """True when this UPF's dataplane isn't eBPF/XDP — every XDP test then grades 'na'."""
    return ctx.upf.dataplane_kind() != "ebpf"


def needs_control(ctx) -> bool:
    return ctx.control is None or not hasattr(ctx.control, "create_sessions")


def na(tid: str, name: str, why: str) -> TestResult:
    return TestResult(tid, name, "na", notes=why)


def intro(ctx):
    """The adapter's live BPF introspection, or None (→ 'na')."""
    return ctx.upf.bpf_introspect()


# --- GTP-U injection at the XDP hook (n3_addr:2152) ---------------------------
def _gtpu(teid: int, ue_ip: str, dst: str = "8.8.8.8", payload: int = 64,
          malformed: bool = False) -> bytes:
    from scapy.contrib.gtp import GTP_U_Header
    from scapy.all import IP, UDP, Raw
    if malformed:
        # GTP-U header advertising a huge length over a truncated, non-IP body — a parser that
        # trusts the length or dereferences the inner header without bounds-checking crashes.
        return bytes(GTP_U_Header(teid=teid, length=0xFFFF))[:8] + b"\x45\x00\x00\x02"
    inner = IP(src=ue_ip, dst=dst) / UDP(sport=1234, dport=80) / Raw(b"\x00" * payload)
    return bytes(GTP_U_Header(teid=teid) / inner)


def inject_gtpu(ctx, teid: int, ue_ip: str, count: int, malformed: bool = False) -> int:
    """Send ``count`` GTP-U datagrams to the UPF's N3 addr:2152 (lands on the XDP hook).
    Returns the number sent."""
    addr = ctx.upf.n3_addr()
    if not addr:
        return 0
    pkt = _gtpu(teid, ue_ip, malformed=malformed)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = 0
    try:
        for _ in range(count):
            try:
                s.sendto(pkt, (addr, 2152))
                sent += 1
            except OSError:
                break
    finally:
        s.close()
    return sent


# --- pfcpsim session install/delete for the binding tests ---------------------
def install_sessions(ctx, count: int, base_id: int):
    """Install ``count`` pfcpsim sessions; return their (teids, ue_ips). pfcpsim installs a
    specific F-TEID per session (TEID == base_id + i), so the maps gain those exact keys."""
    ctx.control.ensure_associated()
    try:
        ctx.control.delete_sessions_raw(count=count, base_id=base_id)   # clear any stale record
    except Exception:
        pass
    ctx.control.create_sessions(count=count, base_id=base_id)
    return ctx.control.aligned_flows(count, base_id)


def delete_sessions(ctx, count: int, base_id: int) -> None:
    try:
        ctx.control.delete_sessions_raw(count=count, base_id=base_id)
    except Exception:
        pass
