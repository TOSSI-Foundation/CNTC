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


def release_procedure(ctx: RunContext, tid: str, name: str, slot: str,
                      expected: list[str], spec: str) -> TestResult:
    """Judge a UE Context Release, but only once something has actually asked for one.

    The release is not something the gNB does on its own. The AMF initiates it over NGAP
    (TS 23.502 §4.2.6), and only then does the CU-CP release the UE on F1. So if no
    NGAP UEContextReleaseCommand was ever received, the product under test was never asked to
    release anything, and its silence is not a defect.

    This matters on the reference rig. free5GC's AMF accepts a UE-initiated Deregistration and
    then does not send a release command at all; the captures show an NG Reset at teardown
    instead. Judged naively, that records a failure against three requirements on the CU-CP and
    the DU for something the core did not do. 'Never asked' is 'na', exactly as an attach that
    stopped short is.
    """
    o = observation(ctx)
    why = attach_incomplete(o)
    if why:
        return _na(tid, name, f"{why}, so no UE context existed to release")
    ngap = o.get("procs.cucp.ngap")
    if ngap is None:
        return _na(tid, name, "no cucp.ngap capture, so it cannot be told whether a release "
                              "was ever requested")
    if "UEContextReleaseCommand" not in ngap:
        return _na(tid, name, "the core never sent an NGAP UE Context Release Command, so the "
                              "gNB was never asked to release the UE context (core behaviour, "
                              f"not a RAN result) [{spec}]")
    return procedures(ctx, tid, name, slot, expected, spec)


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


def _port_listening(host: str, port: int, kind: str, timeout: float = 20.0) -> bool:
    """Wait until the product is actually accepting on its socket.

    Process-alive is not enough for the SCTP probe: a CU-CP that has started but not yet bound
    its F1-C listener refuses the connection, which would otherwise be misread as a crash.
    """
    if kind != "sctp":
        return True                      # UDP is connectionless; nothing to wait for
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 132)
            s.settimeout(2.0)
            s.connect((host, port))
            s.close()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def no_crash(ctx: RunContext, tid: str, name: str, target: str, kind: str,
             spec: str, needs: tuple[str, ...] = ()) -> TestResult:
    """PASS iff the product survives crude non-compliant input on its own socket.

    The product has to be running AND accepting to be probed, so this brings it up (with any
    peer it depends on) first. If it cannot be established the result is 'na', "we could not
    stand it up" is not evidence of a crash, and reporting it as one would blame the product for
    our own setup. Only a product that was demonstrably up, was probed, and is then gone counts
    as a failure.
    """
    endpoint = ctx.endpoint or ctx.ran.node_endpoint(target)
    if not endpoint or ":" not in endpoint:
        return _na(tid, name, f"no reachable {target} endpoint to probe")
    host, _, port_s = endpoint.partition(":")
    port = int(port_s or 0)

    if ctx.ran.node_alive(target) is None:
        return _na(tid, name, "cannot observe liveness for this product (a crash would be "
                              "undetectable, so this cannot be judged)")

    # bring up whatever this product needs before it can serve (the CU-UP has no E1 peer
    # without the CU-CP, the O-DU no F1 peer, and neither stays up alone)
    # Retried as a unit. The O-DU makes exactly one F1-C association attempt at startup and
    # exits if it fails ("attempt 1/1" in its own log), so any moment where the CU-CP is not
    # accepting costs the whole test. Re-establishing the peer and trying again is the
    # compensation for that, and it is our job rather than the product's.
    for _ in range(3):
        for dep in needs:
            _ensure_running(ctx, dep)
            # Wait for the listener, not the log. The CU-CP's log is empty until it exits, so
            # this wait always ran its full timeout and then continued regardless.
            if dep == "cucp" and hasattr(ctx.ran, "wait_for_listen"):
                ctx.ran.wait_for_listen(_F1C_PORT, 45)
        if _ensure_running(ctx, target):
            break
        time.sleep(3)
    _await_peer(ctx, target)
    if ctx.ran.node_alive(target) is not True:
        return _na(tid, name, f"{target} could not be started for probing, so its robustness "
                              f"cannot be judged (this says nothing about the product)")
    if not _await_listening(host, port, kind):
        # A process that exists but never accepts is usually one caught mid-shutdown, handed
        # over by the previous test. pgrep cannot tell that apart from a healthy one, so rather
        # than give up, take it down properly and bring up a fresh instance once.
        ctx.ran.stop(target, graceful=False)
        time.sleep(3)
        _ensure_running(ctx, target)
        _await_peer(ctx, target)
        if not _await_listening(host, port, kind):
            return _na(tid, name, f"{target} never accepted on {host}:{port}, even after a "
                                  f"restart, so the probe could not be delivered")

    # Up is not the same as stable. A product on its way down for its own reasons (its E1 peer
    # never came up, a dependency died) would vanish during the probe and be recorded as a
    # remote DoS. So it is held through a settling window and re-checked before anything is sent.
    if not _settled(ctx, target, host, port, kind):
        return _na(tid, name, f"{target} does not stay up in this rig even with nothing sent to "
                              f"it, so a crash could not be attributed to a probe (this says "
                              f"nothing about the product's robustness)")

    sent, how = (_sctp_garbage(host, port) if kind == "sctp" else _udp_garbage(host, port))
    if not sent:
        return _na(tid, name, f"could not deliver the probe to {host}:{port} ({how})")
    time.sleep(3)
    alive_after = ctx.ran.node_alive(target)
    if alive_after is True:
        return TestResult(tid, name, "pass",
                          metrics={"sent": sent, "alive_after": True,
                                   "probe": f"{kind} {host}:{port}"},
                          notes=f"{how}; {target} alive after = True (no crash) [{spec}]")

    # The product is gone. Accusing it of a remote DoS is a strong claim, so it has to survive a
    # control: same rig, same wait, nothing sent. If it dies unprompted too, the probe is not the
    # demonstrated cause and the honest answer is 'na', not 'fail'.
    if not _survives_control(ctx, target):
        return _na(tid, name, f"{target} went away after the probe, but it also went away in a "
                              f"control run with nothing sent to it, so the probe is not the "
                              f"demonstrated cause")
    return TestResult(tid, name, "fail",
                      metrics={"sent": sent, "alive_after": alive_after,
                               "probe": f"{kind} {host}:{port}", "control": "survived"},
                      notes=f"{how}; {target} alive after = {alive_after} (CRASHED, remote DoS, "
                            f"confirmed against a no-probe control run) [{spec}]")


# A product that has no peer on the interface it exists to serve will not stay up, so probing it
# would measure our own bring-up rather than its robustness. The O-CU-UP is the case that bites:
# without an E1 association to the CU-CP it exits on its own within seconds.
def _ensure_running(ctx: RunContext, target: str) -> bool:
    """Get a product into a freshly running state, even if the previous one is still dying.

    ``start`` declines when the process still exists, which is right for "it is already up" and
    wrong for "it is on its way down". A test that stops a product and hands over to the next
    one leaves exactly that: the old instance is alive for a moment longer, the start is
    declined, and the survivor then stops listening as it finishes exiting. The next test sees a
    process that is running and never accepts, and reports that it could not probe it.
    """
    if ctx.ran.node_alive(target) is True and _serving(ctx, target):
        return True
    # Alive but not serving means mid-shutdown. Take it down for certain before starting a new
    # one, or ``start`` declines and the survivor finishes exiting under the next test's feet.
    ctx.ran.stop(target, graceful=False)
    for _ in range(15):
        if ctx.ran.node_alive(target) is not True:
            break
        time.sleep(1)
    ctx.ran.start(target)
    for _ in range(25):
        if ctx.ran.node_alive(target) is True:
            return _serving(ctx, target)
        time.sleep(1)
    return False


def _serving(ctx: RunContext, target: str) -> bool:
    """Is the product actually serving, not merely present?

    Only the CU-CP can be asked this cheaply, and it is the one that matters: it is the sole
    listener in the split, and the O-DU makes exactly one F1-C association attempt at startup
    ("attempt 1/1" in its own log) before giving up and exiting. Handing the DU a CU-CP that is
    still shutting down therefore kills the DU, and the run then reports that the DU could not
    be started, which reads as a fault in the DU.
    """
    if target != "cucp" or not hasattr(ctx.ran, "wait_for_listen"):
        return True
    # Checked twice with a gap. A CU-CP that is shutting down still shows a LISTEN socket for a
    # moment, and a single look cannot tell that apart from a healthy listener. The DU gets one
    # attempt, so handing it a socket that is about to close costs the whole test.
    if not ctx.ran.wait_for_listen(_F1C_PORT, 3):
        return False
    time.sleep(2)
    return ctx.ran.wait_for_listen(_F1C_PORT, 2)


_F1C_PORT = 38472                     # TS 38.472, the CU-CP's F1-C listener
_PEER_PORT = {"cuup": 38462,           # TS 38.462 (E1), to the CU-CP
              "du": 38472}             # TS 38.472 (F1-C), to the CU-CP


def _await_peer(ctx: RunContext, target: str) -> None:
    """Give the product the peer association it needs before it is judged on staying up.

    Waited for on the socket, not in the log: OCUDU buffers its logs, so an association that
    succeeded can still be invisible in the file, and polling the log reports failure for
    something that worked.
    """
    port = _PEER_PORT.get(target)
    if port is None or not hasattr(ctx.ran, "wait_for_association"):
        return
    if ctx.ran.wait_for_association(port, 30):
        return
    # It can lose the race to the CU-CP's listener and does not retry by itself.
    ctx.ran.stop(target)
    time.sleep(3)
    ctx.ran.start(target)
    ctx.ran.wait_for_association(port, 30)


# How long a product must stay up, untouched, before its disappearance can be blamed on a probe.
_SETTLE_S = 5.0


def _await_listening(host: str, port: int, kind: str, timeout: float = 25.0) -> bool:
    """Poll until the product accepts on its socket. A process that exists is not yet a process
    that has bound its listener, and judging it in that gap blamed the product for our timing."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_listening(host, port, kind):
            return True
        time.sleep(1)
    return False


def _settled(ctx: RunContext, target: str, host: str, port: int, kind: str) -> bool:
    """True iff the product is still running AND still accepting after a settling window."""
    time.sleep(_SETTLE_S)
    return ctx.ran.node_alive(target) is True and _port_listening(host, port, kind)


def _survives_control(ctx: RunContext, target: str) -> bool:
    """The control for a crash claim: restart the product and send it nothing at all.

    If it stays up here but died when probed, the probe is the difference between the two runs.
    If it dies here too, it is simply not staying up in this rig and no crash can be attributed.
    """
    ctx.ran.start(target)
    for _ in range(25):
        if ctx.ran.node_alive(target) is True:
            break
        time.sleep(1)
    if ctx.ran.node_alive(target) is not True:
        return False
    time.sleep(_SETTLE_S + 3)
    return ctx.ran.node_alive(target) is True


# --- transport protection ---------------------------------------------------------
def _ipsec_sas() -> list[tuple[str, str]] | None:
    """Every IPsec SA on the host as (src, dst). None when the state cannot be read."""
    try:
        r = subprocess.run(["sudo", "-n", "ip", "xfrm", "state"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "src" and parts[2] == "dst":
            out.append((parts[1], parts[3]))
    return out


def transport_protected(ctx: RunContext, tid: str, name: str, iface: str,
                        spec: str) -> TestResult:
    """PASS iff this interface's traffic is actually covered by an IPsec SA.

    TS 33.523 points at the TS 33.117 4.2.3.2.4 test: the interface must offer confidentiality,
    integrity and replay protection. Two things have to be true before that can be judged, and
    getting either wrong produces a confident but worthless result.

    First, the interface has to be exposed, and whether it is depends on how the operator
    deployed the products rather than on the products themselves. A rig that co-locates the CU
    and the DU runs F1 between loopback addresses, where the traffic never reaches a network and
    nobody who is not already root on the host can see it. Reporting that as "carried in the
    clear" reads as a product defect and is not one: the requirement cannot be certified from
    that deployment, and the tester needs to be told what would let them certify it.

    Second, an SA has to cover *this* interface. Counting SAs on the host and calling every
    interface protected would pass F1 because someone had configured IPsec for N2.
    """
    addrs = []
    if hasattr(ctx.ran, "interface_addrs"):
        try:
            addrs = ctx.ran.interface_addrs(iface)
        except Exception:  # noqa: BLE001
            addrs = []
    exposed = [a for a in addrs if not a.startswith("127.")]
    if addrs and not exposed:
        return _na(tid, name,
                   f"{iface} runs only between loopback addresses ({', '.join(addrs)}), so this "
                   f"deployment co-locates the endpoints and the traffic never leaves the host. "
                   f"Its transport protection is neither exercised nor observable here. Deploy "
                   f"the products on separate hosts to certify this requirement [{spec}]")

    sas = _ipsec_sas()
    if sas is None:
        return _na(tid, name, "cannot read the host's IPsec state (`ip xfrm state`), so "
                              f"{iface} protection cannot be judged")
    covering = [f"{a}->{b}" for a, b in sas
                if (not exposed) or a in exposed or b in exposed]
    if covering:
        return TestResult(tid, name, "pass",
                          metrics={"ipsec_sas": len(covering), "endpoints": exposed or addrs},
                          notes=f"{iface} is covered by {len(covering)} IPsec SA(s) "
                                f"({', '.join(covering[:3])}) [{spec}]")
    where = ", ".join(exposed) if exposed else "this interface"
    detail = f" (host holds {len(sas)} SA(s), none covering {where})" if sas else ""
    return TestResult(tid, name, "fail",
                      metrics={"ipsec_sas": 0, "endpoints": exposed or addrs,
                               "host_sas": len(sas)},
                      notes=f"{iface} leaves the host on {where} with no IPsec SA covering it, "
                            f"so it is carried in the clear (no confidentiality / integrity / "
                            f"replay protection){detail} [{spec}]")
