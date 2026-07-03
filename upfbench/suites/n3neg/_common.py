"""Shared helpers for the N3 data-plane negative/robustness suite.

A known session (specific F-TEID + UE-IP) is installed via pfcpsim and the BESS egress
short-circuit applied, so VALID GTP-U on that TEID forwards to core TX (the liveness
reference) while malformed / unknown-TEID / bad-PSC packets must be dropped without
crashing the UPF.

Packets are crafted in a CLEAN subprocess (``_pktgen.py``) using the system scapy and
handed back as raw bytes, because the TRex generator's bundled scapy-2.4.3 can't build the
5G PSC ext-header and can't coexist with the system scapy in one process (see _pktgen.py).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from upfbench.suites.load._flows import session_flows

_PKTGEN = str(Path(__file__).parent / "_pktgen.py")


def needs(ctx) -> bool:
    return (ctx.control is None or ctx.traffic is None
            or not hasattr(ctx.control, "create_sessions")
            or not hasattr(ctx.traffic, "send_burst"))


def _establish(ctx, base_id):
    """Install (or re-install, after a crash) one known PFCP session + the egress
    short-circuit, and return its (teid, ue_ip). Retries once if a delayed crash from the
    previous test lands mid-install (bessd briefly 'container not found')."""
    for attempt in range(2):
        try:
            ctx.control.ensure_associated()
            try: ctx.control.delete_sessions_raw(count=1, base_id=base_id)  # clear stale record
            except Exception: pass
            ctx.control.create_sessions(count=1, base_id=base_id)
            if hasattr(ctx.upf, "egress_shortcircuit_install"):
                ctx.upf.egress_shortcircuit_install()
            return session_flows(base_id, 1, ctx.control.ue_pool)
        except Exception:
            if attempt == 1 or not hasattr(ctx.upf, "wait_healthy"):
                raise
            ctx.upf.wait_healthy()   # a buffered crash fired; let bessd come back, retry


def setup(ctx, base_id, frame=256):
    """Install one known session + the egress short-circuit; build every packet variant
    (as raw bytes) in a clean subprocess; return (session-state, {name: bytes})."""
    if hasattr(ctx.upf, "wait_healthy"):     # a prior test may have crashed+restarted bessd
        ctx.upf.wait_healthy()
    teids, ue_ips = _establish(ctx, base_id)
    e = ctx.cfg.upf.extra
    s = {"teid": int(teids[0]), "ue_ip": ue_ips[0],
         "dst_mac": (e.get("n3_remote_mac") or (ctx.traffic._resolve_mac() if hasattr(ctx.traffic, "_resolve_mac") else "00:11:22:33:44:33")),
         "src_mac": e.get("trex_src_mac", "00:11:22:33:44:35"),
         "gnb_ip": e.get("gnb_ip", "192.168.252.10"),
         "remote_ip": e.get("n3_remote_ip", "192.168.252.3"),
         "n6": _n6(ctx), "ff": ctx.upf.fwd_field()}
    pkts = _build_packets(s, frame)
    return s, pkts


# --- crash detection / recovery (a robustness suite must survive the SUT crashing) ----
def restarts(ctx) -> int:
    """Current bessd restart count (0 if the adapter can't report it)."""
    return ctx.upf.restart_count() if hasattr(ctx.upf, "restart_count") else 0


def crashed_since(ctx, base) -> bool:
    """True if the data plane crashed (k8s restarted bessd) since `base` — or if it's
    simply not responsive right now."""
    if hasattr(ctx.upf, "restart_count") and restarts(ctx) > base:
        return True
    return not ctx.upf.healthy()


def settle_and_check(ctx, base, secs=20) -> bool:
    """Poll for up to `secs` for a crash to manifest. A segfault in the BESS worker can lag
    a second or two behind the burst that triggers it (the worker processes the queued
    packet, then k8s notices the exit), so we don't trust a single immediate check."""
    deadline = time.time() + secs
    while True:
        if crashed_since(ctx, base):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)


def crash_reason(ctx) -> str:
    return ctx.upf.crash_reason() if hasattr(ctx.upf, "crash_reason") else ""


def recover(ctx, base_id) -> int:
    """After a detected crash: wait for bessd to come back, re-establish the session +
    short-circuit, and return the new restart-count baseline."""
    if hasattr(ctx.upf, "wait_healthy"):
        ctx.upf.wait_healthy()
    _establish(ctx, base_id)
    return restarts(ctx)


def _build_packets(s, frame) -> dict:
    """Run _pktgen.py in a child process (system scapy only — no TRex path) and decode the
    base64 variants to raw bytes."""
    params = {"teid": s["teid"], "ue_ip": s["ue_ip"], "dst_mac": s["dst_mac"],
              "src_mac": s["src_mac"], "gnb_ip": s["gnb_ip"], "remote_ip": s["remote_ip"],
              "frame": int(frame)}
    # Strip any TRex automation path from the child's import path so it gets the system
    # scapy (2.4.4, with PSC support), not TRex's bundled 2.4.3.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in env.get("PYTHONPATH", "").split(os.pathsep)
        if p and "trex" not in p.lower())
    out = subprocess.run([sys.executable, _PKTGEN], input=json.dumps(params),
                         capture_output=True, text=True, env=env, check=True)
    return {k: base64.b64decode(v) for k, v in json.loads(out.stdout).items()}


def teardown(ctx, base_id):
    if hasattr(ctx.upf, "egress_shortcircuit_remove"):
        try: ctx.upf.egress_shortcircuit_remove()
        except Exception: pass
    try: ctx.control.delete_sessions_raw(count=1, base_id=base_id)
    except Exception: pass


def _n6(ctx):
    pref = ctx.cfg.upf.n6_iface or "core"
    for k in ctx.upf.port_counters():
        if k.startswith(pref):
            return k
    raise RuntimeError("no N6 port")


def core_tx(ctx, s):
    return ctx.upf.port_counters()[s["n6"]][s["ff"]]


def fwd(ctx, s, pkt_bytes, count):
    """Send `count` of raw `pkt_bytes`; return (forwarded_delta, sent)."""
    b = core_tx(ctx, s)
    sent = ctx.traffic.send_burst(pkt_bytes, count)
    return core_tx(ctx, s) - b, sent
