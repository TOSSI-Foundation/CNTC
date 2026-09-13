"""Assertion helpers shared by the PNF and VNF suites.

The honesty rules are the CU/DU ones and are imported rather than restated: evidence that is
missing, or a procedure this stimulus never triggered, is ``na`` with the real reason, never a
pass and never a fail. Only something that should have happened and demonstrably did not is a
fail.

What is new here is that **one capture judges two products, separated by direction**, so every
helper takes the side it is asking about. That separation is what makes an attribution possible:
when the P5 setup stalls, the capture says whether the VNF failed to ask or the PNF failed to
answer, and those are findings against different vendors.

The second rule specific to this interface: **a product cannot be failed for not answering a
question it was never asked.** The VNF drives every P5 procedure, so a missing PNF_CONFIG.response
means one of two very different things depending on whether a PNF_CONFIG.request was ever sent.
``pnf_answers`` checks the request first for exactly that reason.
"""
from __future__ import annotations

from cntc_common.results import TestResult
from ranbench.observers.nfapi import PNF_TO_VNF, VNF_TO_PNF  # noqa: F401  (re-exported)
from ranbench.suites.base import RunContext
from ranbench.suites.ran_common import _na, attach_incomplete, observation  # noqa: F401

PNF, VNF = "pnf", "vnf"


def _procs(o: dict, iface: str, side: str) -> list[str] | None:
    return o.get(f"procs.{iface}.{side}")


def _capture_missing(o: dict, iface: str) -> str | None:
    """The reason this interface cannot be judged at all, or None."""
    if o.get("error"):
        return f"attach unavailable: {o['error']}"
    if o.get("decode_error"):
        return o["decode_error"]
    if _procs(o, iface, PNF) is None and _procs(o, iface, VNF) is None:
        return (f"no {iface.upper()} capture to judge from (nFAPI carries no evidence unless "
                f"the capture was open before the products started)")
    return None


# --- P5 procedures ----------------------------------------------------------------
def pnf_answers(ctx: RunContext, tid: str, name: str, iface: str,
                request: str, response: str, spec: str) -> TestResult:
    """PASS iff the PNF answered a request the VNF actually sent.

    The request is checked first. A PNF that sent no CONFIG.response because it received no
    CONFIG.request has done nothing wrong, and recording that as a failure would blame the PNF
    for the VNF's omission. That distinction is the whole reason direction is tracked.
    """
    o = observation(ctx)
    why = _capture_missing(o, iface)
    if why:
        return _na(tid, name, why)
    asked = _procs(o, iface, VNF) or []
    answered = _procs(o, iface, PNF) or []
    if request not in asked:
        return _na(tid, name, f"the VNF never sent {request}, so the PNF was never asked to "
                              f"{response.split('.')[0]} and cannot be judged on it (a VNF "
                              f"result, not a PNF one) [{spec}]")
    if response in answered:
        return TestResult(tid, name, "pass", metrics={"request": request, "response": response},
                          notes=f"{request} -> {response} observed on {iface.upper()} [{spec}]")
    return TestResult(tid, name, "fail", metrics={"request": request, "seen": answered[:20]},
                      notes=f"the VNF sent {request} but the PNF never answered with "
                            f"{response} [{spec}]")


def vnf_issues(ctx: RunContext, tid: str, name: str, iface: str, message: str,
               spec: str, after: str | None = None) -> TestResult:
    """PASS iff the VNF issued this message, optionally only after ``after`` was answered.

    ``after`` expresses the ordering SCF225 requires of the setup sequence: a VNF that asks the
    PHY to start before the device it lives on has been configured is driving the procedure out
    of order, and that is visible without decoding a single IE.
    """
    o = observation(ctx)
    why = _capture_missing(o, iface)
    if why:
        return _na(tid, name, why)
    issued = _procs(o, iface, VNF) or []
    if message not in issued:
        return TestResult(tid, name, "fail", metrics={"seen": issued[:20]},
                          notes=f"the VNF never issued {message} on {iface.upper()} [{spec}]")
    if after is None:
        return TestResult(tid, name, "pass", metrics={"message": message},
                          notes=f"{message} issued on {iface.upper()} [{spec}]")
    nf, paths = o.get("nfapi"), (o.get("pcaps") or {})
    if nf is None or iface not in paths:
        return _na(tid, name, f"cannot time {message} against {after} without the capture")
    t_msg = nf.first_time(paths[iface], message, VNF_TO_PNF)
    t_after = nf.first_time(paths[iface], after, PNF_TO_VNF)
    if t_after is None:
        return _na(tid, name, f"{after} was never observed, so the ordering of {message} "
                              f"against it cannot be judged [{spec}]")
    if t_msg >= t_after:
        return TestResult(tid, name, "pass",
                          metrics={"message": message, "after": after,
                                   "delta_s": round(t_msg - t_after, 4)},
                          notes=f"{message} issued {t_msg - t_after:.3f}s after {after} [{spec}]")
    return TestResult(tid, name, "fail",
                      metrics={"message": message, "after": after,
                               "delta_s": round(t_msg - t_after, 4)},
                      notes=f"{message} was issued {t_after - t_msg:.3f}s BEFORE {after}, so "
                            f"the setup sequence ran out of order [{spec}]")


def observed(ctx: RunContext, tid: str, name: str, iface: str, side: str,
             message: str, spec: str, optional: bool = False) -> TestResult:
    """PASS iff this message appears from this side. The plain presence case."""
    o = observation(ctx)
    why = _capture_missing(o, iface)
    if why:
        return _na(tid, name, why)
    seen = _procs(o, iface, side) or []
    counts = (o.get(f"counts.{iface}") or {})
    if message in seen:
        return TestResult(tid, name, "pass", metrics={message: counts.get(message)},
                          notes=f"{message} observed {counts.get(message, '?')} time(s) from "
                                f"the {side.upper()} on {iface.upper()} [{spec}]")
    if optional:
        return _na(tid, name, f"{message} was not exercised by this stimulus (a compliant peer "
                              f"need not trigger it) [{spec}]")
    return TestResult(tid, name, "fail", metrics={"seen": seen[:20]},
                      notes=f"{message} never observed from the {side.upper()} on "
                            f"{iface.upper()} [{spec}]")


def answered_indication(ctx: RunContext, tid: str, name: str, iface: str,
                        trigger: str, indication: str, spec: str) -> TestResult:
    """PASS iff the PNF returned an indication for work the VNF actually scheduled.

    Same shape as ``pnf_answers`` but for the slot loop: no CRC.indication is only a defect if
    a PUSCH was scheduled in the first place. On a rig where the UE never transmitted, the PHY
    has nothing to report and its silence is correct.
    """
    o = observation(ctx)
    why = _capture_missing(o, iface)
    if why:
        return _na(tid, name, why)
    counts = o.get(f"counts.{iface}") or {}
    scheduled = counts.get(trigger, 0)
    got = counts.get(indication, 0)
    if not scheduled:
        return _na(tid, name, f"the VNF scheduled no {trigger}, so the PNF had nothing to "
                              f"report with {indication} [{spec}]")
    if not got:
        # A scheduled uplink opportunity is not the same as a UE that used one. The L2 keeps
        # issuing UL_TTI for PRACH occasions whether or not anyone is on the cell, so counting
        # those as "work the PHY owed an answer for" fails a PHY that correctly reported
        # nothing about a UE that was never there. Measured on this rig: a run where the UE did
        # not synchronise failed the PNF on CRC and RX_DATA for exactly that reason.
        stalled = attach_incomplete(o)
        if stalled:
            return _na(tid, name, f"no {indication} was returned, but {stalled}, so nothing was "
                                  f"ever transmitted for the PHY to report on [{spec}]")
    if got:
        return TestResult(tid, name, "pass", metrics={trigger: scheduled, indication: got},
                          notes=f"{scheduled} x {trigger} scheduled, {got} x {indication} "
                                f"returned [{spec}]")
    return TestResult(tid, name, "fail", metrics={trigger: scheduled, indication: 0},
                      notes=f"{scheduled} x {trigger} were scheduled but the PNF returned no "
                            f"{indication} at all [{spec}]")


# --- transport --------------------------------------------------------------------
def transport_is(ctx: RunContext, tid: str, name: str, iface: str,
                 expected: str, spec: str) -> TestResult:
    """PASS iff the interface was carried on the transport SCF225 specifies.

    Reported as observed rather than assumed. The capture filter for P5 deliberately does not
    name a transport, so a deployment that carries it on TCP or UDP is visible here instead of
    being invisible to a filter that only matched SCTP.
    """
    o = observation(ctx)
    why = _capture_missing(o, iface)
    if why:
        return _na(tid, name, why)
    got = o.get(f"transport.{iface}") or []
    if not got:
        return _na(tid, name, f"no {iface.upper()} messages were captured, so the transport "
                              f"carrying them cannot be reported")
    if got == [expected]:
        return TestResult(tid, name, "pass", metrics={"transport": got},
                          notes=f"{iface.upper()} is carried on {expected.upper()}, as "
                                f"SCF225 specifies [{spec}]")
    return TestResult(tid, name, "fail", metrics={"transport": got, "expected": expected},
                      notes=f"{iface.upper()} is carried on {', '.join(got).upper()} but "
                            f"SCF225 specifies {expected.upper()} [{spec}]")


# --- the slot loop ----------------------------------------------------------------
def slot_sequence(ctx: RunContext, tid: str, name: str, spec: str) -> TestResult:
    """PASS iff SFN and slot advance without a gap, and stay inside their 3GPP ranges.

    This is the timing property that survives a simulated radio. Every slot indication must be
    the successor of the one before it, SFN wrapping at 1024 and slot at the numerology's count,
    and a break means the PHY skipped or repeated a slot, which the L2 above it cannot recover.
    """
    o = observation(ctx)
    s = o.get("slot")
    if not s:
        return _na(tid, name, "no P7 capture to judge the slot loop from")
    if not s.get("frames"):
        return _na(tid, name, "no SLOT.indication was captured, so the PHY never ran a slot "
                              "loop and there is no sequence to judge")
    problems = []
    if s["sfn_max"] > 1023 or s["sfn_min"] < 0:
        problems.append(f"SFN out of range ({s['sfn_min']}..{s['sfn_max']}, must be 0..1023)")
    if s["breaks"]:
        problems.append(f"{s['breaks']} of {s['transitions']} slot transitions were not "
                        f"consecutive")
    if problems:
        return TestResult(tid, name, "fail", metrics=s, notes="; ".join(problems) + f" [{spec}]")
    return TestResult(tid, name, "pass", metrics=s,
                      notes=f"{s['frames']} slot indications, SFN {s['sfn_min']}..{s['sfn_max']}, "
                            f"slot {s['slot_min']}..{s['slot_max']}, no gaps in "
                            f"{s['transitions']} transitions [{spec}]")


def slot_rate(ctx: RunContext, tid: str, name: str, nominal_hz: float,
              spec: str) -> TestResult:
    """Report the slot cadence, and refuse to certify it on a simulated radio.

    Under a radio simulator the slot clock is driven by how fast samples are produced, which is
    a property of the simulation and of the host it runs on rather than of the PHY. Measured on
    this rig it moved with UE presence and differed by more than threefold between two runs of
    the same binaries. Grading a product on that would say something about the tester's CPU.

    So the figure is measured and recorded, and the verdict is 'na' with the reason, unless the
    deployment declares a real radio through the ``radio`` knob.
    """
    o = observation(ctx)
    s = o.get("slot")
    if not s or not s.get("frames"):
        return _na(tid, name, "no SLOT.indication was captured, so the cadence cannot be "
                              "measured")
    rate = s.get("rate_hz")
    simulated = str((ctx.knobs or {}).get("radio", "simulated")).lower() != "hardware"
    metrics = {**s, "nominal_hz": nominal_hz}
    if simulated:
        return TestResult(tid, name, "na", metrics=metrics,
                          notes=f"the PHY issued {rate}/s against a nominal {nominal_hz}/s, but "
                                f"this deployment drives it from a radio simulator whose sample "
                                f"clock sets the cadence, so the figure measures the simulation "
                                f"and the host, not the PHY. Certifying this requirement needs "
                                f"a real radio [{spec}]")
    tolerance = 0.05
    ok = rate is not None and abs(rate - nominal_hz) <= nominal_hz * tolerance
    return TestResult(tid, name, "pass" if ok else "fail", metrics=metrics,
                      notes=f"{rate}/s against a nominal {nominal_hz}/s "
                            f"({'within' if ok else 'outside'} {int(tolerance*100)}%) [{spec}]")


def p7_after_start(ctx: RunContext, tid: str, name: str, spec: str) -> TestResult:
    """PASS iff no P7 traffic preceded the PHY's own START.response.

    A state-machine test rather than a liveness check. SCF222 has the PHY answer START.request
    and only then run the slot loop; a PHY emitting P7 before it has confirmed the transition is
    not in the state it is claiming to be in.

    The two interfaces are captured separately, so the comparison is made on absolute time.
    """
    o = observation(ctx)
    nf, paths = o.get("nfapi"), (o.get("pcaps") or {})
    if nf is None or "p5" not in paths or "p7" not in paths:
        return _na(tid, name, "both the P5 and P7 captures are needed to order them, and at "
                              "least one is missing")
    e5, e7 = o.get("epoch0.p5"), o.get("epoch0.p7")
    t_start = nf.first_time(paths["p5"], "START.response", PNF_TO_VNF)
    t_p7 = o.get("p7_first_t")
    if t_start is None:
        return _na(tid, name, "the PHY never sent START.response, so there is no transition to "
                              "order P7 against [" + spec + "]")
    if t_p7 is None or e5 is None or e7 is None:
        return _na(tid, name, "no P7 traffic was captured, so there is nothing to order")
    delta = (e7 + t_p7) - (e5 + t_start)
    if delta >= 0:
        return TestResult(tid, name, "pass", metrics={"delta_s": round(delta, 4)},
                          notes=f"the first P7 message followed START.response by "
                                f"{delta:.3f}s [{spec}]")
    return TestResult(tid, name, "fail", metrics={"delta_s": round(delta, 4)},
                      notes=f"P7 began {-delta:.3f}s BEFORE the PHY answered START.response, so "
                            f"the slot loop was running while the PHY still reported itself as "
                            f"not started [{spec}]")


# --- did the VNF deliver its slot requests in time? --------------------------------
_SLOTS_PER_FRAME = 20          # numerology 1 (30 kHz SCS)
_WRAP = 1024 * _SLOTS_PER_FRAME


def _sfn_slot(body: bytes) -> tuple[int, int] | None:
    """SLOT.indication, DL_TTI.request and UL_TTI.request all open their body with SFN, slot."""
    if len(body) < 4:
        return None
    return int.from_bytes(body[0:2], "big"), int.from_bytes(body[2:4], "big")


def tti_lead(ctx: RunContext, tid: str, name: str, message: str, spec: str) -> TestResult:
    """PASS iff every slot request named a slot the PHY had not already passed.

    A DL_TTI or UL_TTI request carries the SFN and slot it is *for*. The PHY announces the slot
    it is *in* with SLOT.indication. Compare the two and the VNF's timing is directly visible:
    a request naming a slot that has already gone by cannot be served and the PHY discards it,
    which is the VNF running late rather than the PHY misbehaving.

    This is measured against the slot loop rather than against wall-clock, so it stays valid on
    a simulated radio, where absolute cadence is the simulator's property but slot ordering is
    still the real thing the two ends agree on.
    """
    o = observation(ctx)
    nf, paths = o.get("nfapi"), (o.get("pcaps") or {})
    if nf is None or "p7" not in paths:
        return _na(tid, name, "no P7 capture to judge slot-request timing from")
    msgs = nf.messages(paths["p7"])
    if msgs is None:
        return _na(tid, name, "the P7 capture could not be read")
    current: int | None = None
    total = late = marginal = 0
    worst = 0
    for m in msgs:
        pair = _sfn_slot(m.body)
        if m.name == "SLOT.indication":
            if pair:
                current = pair[0] * _SLOTS_PER_FRAME + pair[1]
            continue
        if m.name != message or pair is None or current is None:
            continue
        target = pair[0] * _SLOTS_PER_FRAME + pair[1]
        # Slot numbering wraps, so a small negative difference is lateness and a large one is
        # the wrap. Half the cycle is the only defensible split point.
        lead = (target - current + _WRAP) % _WRAP
        if lead > _WRAP // 2:
            lead -= _WRAP
        total += 1
        if lead < 0:
            # One slot of apparent lateness is the resolution of this method, not evidence.
            # The two directions are captured independently, so a request answering slot N can
            # be timestamped after the indication for N+1 that overtook it in flight. Measured
            # on this rig: the VNF answered within 0.2 ms and the PHY emitted the next
            # indication 0.12 ms into that gap, which is not the VNF running late. Only a
            # request that missed by two slots or more is unambiguous.
            if lead <= -2:
                late += 1
            else:
                marginal += 1
            worst = min(worst, lead)
    if not total:
        return _na(tid, name, f"no {message} carried a readable slot number, so its timing "
                              f"could not be judged [{spec}]")
    metrics = {"message": message, "checked": total, "late": late,
               "within_capture_resolution": marginal, "worst_lead_slots": worst}
    if late:
        # Host descheduling contaminates this, and measuring in slots does not protect against
        # it. Measuring in slots defends against a simulated *cadence* (the slot clock running
        # slow but evenly). It does not defend against a non-isolated host freezing the PNF and
        # the VNF independently: when the PHY thread is scheduled while the VNF thread is still
        # descheduled, the PHY's slot clock advances and the VNF's in-flight requests then name
        # slots it skipped. That is the OS scheduler, not the VNF, and it is exactly what the
        # PNF's own PNF-TIME-01 already refuses to grade on a simulated radio.
        #
        # So only fail the VNF for lateness when there is no evidence the whole stack was
        # stalled. The SLOT.indication stream is that evidence: a conformant 0.5 ms slot loop
        # never gaps by tens of slots, so a gap far beyond the slot period is a host freeze that
        # desynchronised the two independently captured directions. On an isolated rig
        # (isolcpus + CPU pinning) or a real radio, no such gap exists and this fails normally.
        simulated = str((ctx.knobs or {}).get("radio", "simulated")).lower() != "hardware"
        slot_ms = 1000.0 / float((ctx.knobs or {}).get("nominal_slot_rate_hz", 2000) or 2000)
        st = nf.slot_timing(paths["p7"]) or {}
        max_gap, p50 = st.get("gap_max_ms"), st.get("gap_p50_ms")
        stall_ms = max(10.0, slot_ms * 20)         # 20 skipped slots: no real-time loop does this
        if simulated and max_gap and max_gap > stall_ms:
            metrics.update({"slot_gap_max_ms": max_gap, "slot_gap_p50_ms": p50})
            return _na(tid, name,
                       f"{late} of {total} {message} named a slot the PHY had passed (worst "
                       f"{worst}), but the SLOT.indication stream shows host stalls up to "
                       f"{max_gap:.0f} ms against a {p50:.2f} ms median on a simulated radio, so "
                       f"the PHY and the VNF were descheduled together by a non-isolated host and "
                       f"the lateness cannot be attributed to the VNF. On an isolated rig "
                       f"(isolcpus + CPU pinning) or a real radio this test grades normally [{spec}]")
        return TestResult(tid, name, "fail", metrics=metrics,
                          notes=f"{late} of {total} {message} named a slot the PHY had already "
                                f"passed by two or more (worst {worst}), so the PHY could not "
                                f"serve them [{spec}]")
    note = f"all {total} {message} arrived for a slot still ahead of the PHY"
    if marginal:
        note += (f", except {marginal} that trailed by a single slot, which is inside the "
                 f"resolution of an independently captured pair of directions")
    return TestResult(tid, name, "pass", metrics=metrics, notes=note + f" [{spec}]")
