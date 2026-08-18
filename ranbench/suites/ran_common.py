"""Assertion helpers shared by the O-CU-CP, O-CU-UP and O-DU suites.

Every RAN test reduces to one of a few shapes, and keeping them here means all three product
classes are judged by the same rule:

  * ``procedures``, did these protocol messages appear on this interface?
  * ``detail``, did a message carrying this detail appear (RRC/NAS names ride inside the
                      F1AP and NGAP containers)?
  * ``milestone``, did the UE reach this point of the attach?
  * ``no_crash``, did the product survive crude, non-compliant input?
  * ``transport``, is the interface protected by IPsec (TS 33.523 / TS 33.117 §4.2.3.2.4)?

The honesty rules from the control plane carry over unchanged: evidence that is missing (no
capture, procedure never exercised by this stimulus, tool absent) is ``na`` with the real reason,
never a pass and never a fail. Only a procedure that *should* have happened and demonstrably did
not is a fail.
"""
from __future__ import annotations

import socket
import subprocess
import time

from cntc_common.results import TestResult
from ranbench.suites.base import RunContext


def observation(ctx: RunContext) -> dict:
    """The single cached attach every suite reads. Runs the attach on first use."""
    if ctx.ue is None or not hasattr(ctx.ue, "observe_attach"):
        return {"error": "no UE driver wired (set drivers.ue in the campaign config)"}
    return ctx.ue.observe_attach(ctx.ran, ctx.observer)


def _na(tid, name, why) -> TestResult:
    return TestResult(tid, name, "na", notes=why)


# The attach is a chain: nothing after a broken link can be observed, and absence of evidence
# there says nothing about the product. Each stage names the milestone that must have been
# reached for evidence beyond it to be meaningful.
_STAGES = [
    ("synchronized", "the UE never synchronised to the cell"),
    ("rrc_connected", "the UE never reached RRC_CONNECTED"),
    ("registration_accept", "the UE never completed registration with the core"),
    ("pdu_session", "the UE never got a PDU session"),
]


def attach_incomplete(o: dict) -> str | None:
    """The reason the attach stopped short, or None if it ran to completion.

    This is what separates 'the product failed to do X' from 'the stimulus never got far
    enough to see X'. The first is a fail; the second can only ever be 'na'.
    """
    if o.get("error"):
        return f"attach unavailable: {o['error']}"
    for key, why in _STAGES:
        if o.get(key) is not True:
            return why
    return None


def procedures(ctx: RunContext, tid: str, name: str, slot: str, expected: list[str],
               spec: str, optional: bool = False) -> TestResult:
    """PASS iff every expected protocol message appears in ``slot`` (e.g. 'cucp.ngap').

    ``optional=True`` marks a procedure this stimulus does not necessarily trigger: absent
    means 'not exercised' (na), not 'the product failed to do it'.
    """
    o = observation(ctx)
    if o.get("error"):
        return _na(tid, name, f"attach unavailable: {o['error']}")
    procs = o.get(f"procs.{slot}")
    if procs is None:
        return _na(tid, name, f"no {slot} capture to judge from "
                              f"(is that pcap enabled in the RAN's config?)")
    missing = [p for p in expected if p not in procs]
    if not missing:
        return TestResult(tid, name, "pass", metrics={"procedures": expected},
                          notes=f"observed on {slot}: {', '.join(expected)} [{spec}]")
    if optional:
        return _na(tid, name, f"{', '.join(missing)} not exercised by this stimulus "
                              f"(a compliant peer need not trigger it) [{spec}]")
    stalled = attach_incomplete(o)
    if stalled:
        return _na(tid, name, f"{', '.join(missing)} not observed, but {stalled}, the "
                              f"stimulus never reached this procedure, so the product cannot "
                              f"be judged on it [{spec}]")
    return TestResult(tid, name, "fail", metrics={"missing": missing, "seen": procs[:20]},
                      notes=f"missing on {slot}: {', '.join(missing)} [{spec}]")


def detail(ctx: RunContext, tid: str, name: str, slot: str, needles: list[str],
           spec: str, optional: bool = False) -> TestResult:
    """PASS iff a captured message carries each detail (an RRC or NAS message name)."""
    o = observation(ctx)
    if o.get("error"):
        return _na(tid, name, f"attach unavailable: {o['error']}")
    lines = o.get(f"lines.{slot}")
    if not lines:
        return _na(tid, name, f"no {slot} capture to judge from")
    blob = "\n".join(lines).lower()
    missing = [n for n in needles if n.lower() not in blob]
    if not missing:
        return TestResult(tid, name, "pass", metrics={"details": needles},
                          notes=f"observed on {slot}: {', '.join(needles)} [{spec}]")
    if optional:
        return _na(tid, name, f"{', '.join(missing)} not exercised by this stimulus [{spec}]")
    stalled = attach_incomplete(o)
    if stalled:
        return _na(tid, name, f"{', '.join(missing)} not observed, but {stalled}, cannot "
                              f"judge the product on it [{spec}]")
    return TestResult(tid, name, "fail", metrics={"missing": missing},
                      notes=f"not observed on {slot}: {', '.join(missing)} [{spec}]")


def milestone(ctx: RunContext, tid: str, name: str, key: str, ok_note: str,
              spec: str) -> TestResult:
    """PASS iff the UE reached this point of the attach."""
    o = observation(ctx)
    if o.get("error"):
        return _na(tid, name, f"attach unavailable: {o['error']}")
    val = o.get(key)
    if val is None:
        return _na(tid, name, f"the attach did not reach a point where {key} could be judged")
    if val:
        return TestResult(tid, name, "pass", metrics={key: True, "ue_ip": o.get("ue_ip", "")},
                          notes=f"{ok_note} [{spec}]")
    stalled = attach_incomplete(o)
    if stalled and not o.get("synchronized"):
        return _na(tid, name, f"{stalled}, so {key} could not be reached [{spec}]")
    return TestResult(tid, name, "fail", metrics={key: False},
                      notes=f"the UE did not reach {key} [{spec}]")


def gtpu_traffic(ctx: RunContext, tid: str, name: str, slot: str, spec: str,
                 need_qfi: bool = False) -> TestResult:
    """PASS iff user-plane PDUs flowed on this GTP-U interface (and carry a QFI if required)."""
    o = observation(ctx)
    if o.get("error"):
        return _na(tid, name, f"attach unavailable: {o['error']}")
    g = o.get(f"gtpu.{slot}")
    if g is None:
        return _na(tid, name, f"no {slot} capture to judge from")
    if not g.get("has_traffic"):
        stalled = attach_incomplete(o)
        if stalled:
            return _na(tid, name, f"no user plane to observe: {stalled} [{spec}]")
        return TestResult(tid, name, "fail", metrics=g,
                          notes=f"no GTP-U PDUs on {slot} [{spec}]")
    if need_qfi and not g.get("qfis"):
        return TestResult(tid, name, "fail", metrics=g,
                          notes=f"GTP-U flows on {slot} but no PDU Session Container / QFI "
                                f"was present [{spec}]")
    return TestResult(tid, name, "pass", metrics=g,
                      notes=f"{g['frames']} GTP-U PDUs on {slot}, TEIDs {g['teids'][:4]}"
                            + (f", QFI {g['qfis']}" if g.get("qfis") else "") + f" [{spec}]")


# --- robustness -------------------------------------------------------------------
def _sctp_garbage(addr: str, port: int, timeout: float = 4.0) -> tuple[bool, str]:
    """Open an SCTP association and send bytes that are not a valid PDU. A reset is fine, the
    verdict is decided by whether the product is still alive afterwards."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 132)   # IPPROTO_SCTP
        s.settimeout(timeout)
        s.connect((addr, port))
        s.send(b"\xde\xad\xbe\xef" * 64)
        s.close()
        return True, "garbage sent over SCTP"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _udp_garbage(addr: str, port: int) -> tuple[bool, str]:
    """Send malformed GTP-U (bad version/type, truncated) to a user-plane socket."""
    payloads = [b"\xff\xff\x00\x04\x00\x00\x00\x01", b"\x30", b"\x30\xff" + b"\x00" * 6]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2.0)
        for p in payloads:
            s.sendto(p, (addr, port))
        s.close()
        return True, f"{len(payloads)} malformed GTP-U datagrams sent"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def no_crash(ctx: RunContext, tid: str, name: str, target: str, kind: str,
             spec: str) -> TestResult:
    """PASS iff the product survives crude non-compliant input on its own socket.

    The product has to be running to be probed, so this starts it if the measurement already
    tore it down. If liveness cannot be observed at all the result is 'na', an undetectable
    crash must never be scored as a pass.
    """
    endpoint = ctx.endpoint or ctx.ran.node_endpoint(target)
    if not endpoint or ":" not in endpoint:
        return _na(tid, name, f"no reachable {target} endpoint to probe")
    host, _, port_s = endpoint.partition(":")
    port = int(port_s or 0)

    if ctx.ran.node_alive(target) is not True:
        ctx.ran.start(target)
        for _ in range(20):
            if ctx.ran.node_alive(target) is True:
                break
            time.sleep(1)
    alive_before = ctx.ran.node_alive(target)
    if alive_before is None:
        return _na(tid, name, "cannot observe liveness for this product (a crash would be "
                              "undetectable, so this cannot be judged)")
    if alive_before is False:
        return _na(tid, name, f"{target} is not running, so it cannot be probed")

    sent, how = (_sctp_garbage(host, port) if kind == "sctp" else _udp_garbage(host, port))
    time.sleep(2)
    alive_after = ctx.ran.node_alive(target)
    ok = alive_after is True
    return TestResult(tid, name, "pass" if ok else "fail",
                      metrics={"sent": sent, "alive_after": alive_after,
                               "probe": f"{kind} {host}:{port}"},
                      notes=f"{how}; {target} alive after = {alive_after} "
                            f"({'no crash' if ok else 'CRASHED, remote DoS'}) [{spec}]")


# --- transport protection ---------------------------------------------------------
def _ipsec_sa_count() -> int | None:
    """Number of IPsec security associations on the host. None when it cannot be read."""
    try:
        r = subprocess.run(["sudo", "-n", "ip", "xfrm", "state"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return sum(1 for line in r.stdout.splitlines() if line.startswith("src "))


def transport_protected(ctx: RunContext, tid: str, name: str, iface: str,
                        spec: str) -> TestResult:
    """PASS iff the interface's transport is protected (IPsec).

    TS 33.523 points at the TS 33.117 §4.2.3.2.4 test: the interface must offer confidentiality,
    integrity and replay protection. On these interfaces that means IPsec (or DTLS for SCTP).
    We judge it by whether the host holds any IPsec SA at all, no SA means the traffic is in
    the clear, which is a real finding, not a measurement gap.
    """
    n = _ipsec_sa_count()
    if n is None:
        return _na(tid, name, "cannot read the host's IPsec state (`ip xfrm state`), so "
                              f"{iface} protection cannot be judged")
    if n > 0:
        return TestResult(tid, name, "pass", metrics={"ipsec_sas": n},
                          notes=f"{n} IPsec SA(s) present covering {iface} [{spec}]")
    return TestResult(tid, name, "fail", metrics={"ipsec_sas": 0},
                      notes=f"no IPsec SA on the host, {iface} is carried in the clear "
                            f"(no confidentiality / integrity / replay protection) [{spec}]")
