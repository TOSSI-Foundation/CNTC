"""PNF (nFAPI L1 / PHY) Level 1 test cases.

Judged from one nFAPI capture, reading only the PNF -> VNF direction, plus the two ordering
cases that need both. The VNF's own behaviour is judged separately by the VNF suite from the
same file, which is what lets a mixed-vendor deployment attribute a failure to a side.

What is NOT judged here, deliberately: anything the PHY did on the radio. The nFAPI wire carries
what the PHY *reports*, not what it transmitted, so TS 38.211 to 38.214 are absent from the
catalog and no case here pretends to reach them.
"""
from __future__ import annotations

from cntc_common.results import TestResult
from ranbench.suites import fapi_common as fc
from ranbench.suites import ran_common as rc
from ranbench.suites.base import RanTestCase, RunContext

P5, P7 = "p5", "p7"
SCF5 = "SCF225 P5"
SCF222 = "SCF222.10"


# --- P5: the PNF and PHY instance setup state machine ------------------------------
class PnfP501(RanTestCase):
    id, name, target = "PNF-P5-01", "PNF_PARAM exchange, device capabilities advertised", "pnf"
    def run(self, ctx):
        return fc.pnf_answers(ctx, self.id, self.name, P5,
                              "PNF_PARAM.request", "PNF_PARAM.response", SCF5)


class PnfP502(RanTestCase):
    id, name, target = "PNF-P5-02", "PNF_CONFIG exchange, PHY instances configured", "pnf"
    def run(self, ctx):
        return fc.pnf_answers(ctx, self.id, self.name, P5,
                              "PNF_CONFIG.request", "PNF_CONFIG.response", SCF5)


class PnfP503(RanTestCase):
    id, name, target = "PNF-P5-03", "PNF_START exchange, PNF device reaches RUNNING", "pnf"
    def run(self, ctx):
        return fc.pnf_answers(ctx, self.id, self.name, P5,
                              "PNF_START.request", "PNF_START.response", SCF5)


class PnfP504(RanTestCase):
    id, name, target = "PNF-P5-04", "PARAM exchange, PHY capability TLVs reported", "pnf"
    def run(self, ctx):
        return fc.pnf_answers(ctx, self.id, self.name, P5,
                              "PARAM.request", "PARAM.response", f"{SCF222} Table 3-9")


class PnfP505(RanTestCase):
    id, name, target = "PNF-P5-05", "CONFIG accepted with error code MSG_OK", "pnf"

    def run(self, ctx: RunContext):
        """The response must both exist and report success.

        A CONFIG.response carrying an error code is the PHY correctly refusing a configuration
        it cannot serve, which is conformant behaviour but not a configured PHY, so it is
        reported as observed rather than quietly passed. The error code is the first byte of the
        body (SCF222.10 Table 2-25).
        """
        base = fc.pnf_answers(ctx, self.id, self.name, P5,
                              "CONFIG.request", "CONFIG.response", f"{SCF222} Table 2-25")
        if base.status != "pass":
            return base
        o = fc.observation(ctx)
        nf, paths = o.get("nfapi"), (o.get("pcaps") or {})
        body = b""
        if nf is not None and P5 in paths:
            for m in nf.messages(paths[P5]) or []:
                if m.name == "CONFIG.response":
                    body = m.body
                    break
        if not body:
            return TestResult(self.id, self.name, "na",
                              notes="CONFIG.response was observed but carried no readable body, "
                                    "so its error code could not be checked "
                                    f"[{SCF222} Table 2-25]")
        code = body[0]
        if code == 0:
            return TestResult(self.id, self.name, "pass", metrics={"error_code": code},
                              notes=f"CONFIG.response error_code=0 (MSG_OK) [{SCF222} Table 2-25]")
        return TestResult(self.id, self.name, "fail", metrics={"error_code": code},
                          notes=f"the PHY rejected the configuration with error_code={code}, so "
                                f"it never reached the CONFIGURED state [{SCF222} Table 2-25]")


class PnfP506(RanTestCase):
    id, name, target = "PNF-P5-06", "START exchange, PHY instance enters RUNNING", "pnf"
    def run(self, ctx):
        return fc.pnf_answers(ctx, self.id, self.name, P5,
                              "START.request", "START.response", f"{SCF222}.04 §3.2")


class PnfP507(RanTestCase):
    id, name, target = "PNF-P5-07", "No P7 traffic before START.response", "pnf"
    def run(self, ctx):
        return fc.p7_after_start(ctx, self.id, self.name, f"{SCF222} Table 2-25")


class PnfP508(RanTestCase):
    id, name, target = "PNF-P5-08", "Orderly stop on teardown", "pnf"
    def run(self, ctx):
        # A campaign that is killed rather than stopped never asks for a stop, and the PNF's
        # silence then says nothing about it.
        return fc.observed(ctx, self.id, self.name, P5, fc.PNF, "STOP.response",
                           SCF222, optional=True)


class PnfP509(RanTestCase):
    id, name, target = "PNF-P5-09", "P5 carried on SCTP", "pnf"
    def run(self, ctx):
        return fc.transport_is(ctx, self.id, self.name, P5, "sctp", "SCF225 transport")


# --- P7: the slot loop --------------------------------------------------------------
class PnfP701(RanTestCase):
    id, name, target = "PNF-P7-01", "SLOT.indication cadence", "pnf"
    def run(self, ctx):
        # 2000 slots/s at numerology 1 (30 kHz SCS, 0.5 ms slots).
        nominal = float((ctx.knobs or {}).get("nominal_slot_rate_hz", 2000))
        return fc.slot_rate(ctx, self.id, self.name, nominal, SCF222)


class PnfP702(RanTestCase):
    id, name, target = "PNF-P7-02", "SFN and slot monotonic and in range", "pnf"
    def run(self, ctx):
        return fc.slot_sequence(ctx, self.id, self.name, SCF222)


class PnfP703(RanTestCase):
    id, name, target = "PNF-P7-03", "DL_TTI.request accepted", "pnf"
    def run(self, ctx):
        return _accepted(ctx, self.id, self.name, "DL_TTI.request")


class PnfP704(RanTestCase):
    id, name, target = "PNF-P7-04", "UL_TTI.request accepted", "pnf"
    def run(self, ctx):
        return _accepted(ctx, self.id, self.name, "UL_TTI.request")


class PnfP705(RanTestCase):
    id, name, target = "PNF-P7-05", "TX_DATA.request accepted", "pnf"
    def run(self, ctx):
        return _accepted(ctx, self.id, self.name, "TX_DATA.request")


def _accepted(ctx: RunContext, tid: str, name: str, message: str) -> TestResult:
    """The PHY took a slot request without objecting to it.

    What is observable on nFAPI is acceptance, not execution: whether the PDU actually went out
    on the air is a radio question this evidence model cannot reach, and the catalog does not
    claim it. A PHY that cannot serve a request answers ERROR.indication (SCF222.10), so the
    honest test is that the request was made and no error came back for it.
    """
    o = fc.observation(ctx)
    why = fc._capture_missing(o, P7)          # noqa: SLF001, same package
    if why:
        return rc._na(tid, name, why)         # noqa: SLF001
    counts = o.get(f"counts.{P7}") or {}
    sent = counts.get(message, 0)
    errors = counts.get("ERROR.indication", 0)
    if not sent:
        return rc._na(tid, name,              # noqa: SLF001
                      f"the VNF sent no {message}, so the PHY was never asked to accept one "
                      f"[{SCF222}]")
    if errors:
        return TestResult(tid, name, "fail", metrics={message: sent, "errors": errors},
                          notes=f"{sent} x {message} were sent and the PHY returned {errors} "
                                f"ERROR.indication [{SCF222}]")
    return TestResult(tid, name, "pass", metrics={message: sent, "errors": 0},
                      notes=f"{sent} x {message} accepted with no ERROR.indication [{SCF222}]")


class PnfP706(RanTestCase):
    id, name, target = "PNF-P7-06", "RACH.indication on preamble detection", "pnf"

    def run(self, ctx: RunContext):
        o = fc.observation(ctx)
        # No UE on the cell means no preamble, so silence is correct rather than a defect.
        if o.get("synchronized") is not True:
            return rc._na(self.id, self.name,  # noqa: SLF001
                          "the UE never synchronised to the cell, so no preamble was ever "
                          f"transmitted for the PHY to detect [{SCF222}]")
        return fc.observed(ctx, self.id, self.name, P7, fc.PNF, "RACH.indication", SCF222)


class PnfP707(RanTestCase):
    id, name, target = "PNF-P7-07", "CRC.indication for scheduled PUSCH", "pnf"
    def run(self, ctx):
        return fc.answered_indication(ctx, self.id, self.name, P7,
                                      "UL_TTI.request", "CRC.indication", SCF222)


class PnfP708(RanTestCase):
    id, name, target = "PNF-P7-08", "RX_DATA.indication carries the uplink payload", "pnf"
    def run(self, ctx):
        return fc.answered_indication(ctx, self.id, self.name, P7,
                                      "UL_TTI.request", "RX_DATA.indication", SCF222)


class PnfP709(RanTestCase):
    id, name, target = "PNF-P7-09", "UCI.indication for scheduled PUCCH", "pnf"
    def run(self, ctx):
        return fc.answered_indication(ctx, self.id, self.name, P7,
                                      "UL_TTI.request", "UCI.indication", SCF222)


# --- P7 synchronisation and delay management ----------------------------------------
class PnfSync01(RanTestCase):
    id, name, target = "PNF-SYNC-01", "Node synchronisation exchange", "pnf"
    def run(self, ctx):
        return fc.pnf_answers(ctx, self.id, self.name, P7,
                              "DL_NODE_SYNC", "UL_NODE_SYNC", "SCF225 delay management")


class PnfSync02(RanTestCase):
    id, name, target = "PNF-SYNC-02", "TIMING_INFO issued to the VNF", "pnf"
    def run(self, ctx):
        return fc.observed(ctx, self.id, self.name, P7, fc.PNF, "TIMING_INFO",
                           "SCF225 delay management")


class PnfTime01(RanTestCase):
    id, name, target = "PNF-TIME-01", "SLOT.indication jitter", "pnf"

    def run(self, ctx: RunContext):
        """Jitter shares the cadence problem: on a simulated radio it measures the host.

        Reported with the figures so a reader can see them, graded 'na' unless the deployment
        declares real hardware, exactly as the cadence case is.
        """
        o = fc.observation(ctx)
        s = o.get("slot")
        if not s or not s.get("frames"):
            return rc._na(self.id, self.name,   # noqa: SLF001
                          "no SLOT.indication was captured, so jitter cannot be measured")
        simulated = str((ctx.knobs or {}).get("radio", "simulated")).lower() != "hardware"
        if simulated:
            return TestResult(self.id, self.name, "na", metrics=s,
                              notes=f"inter-arrival p50 {s.get('gap_p50_ms')} ms, p99 "
                                    f"{s.get('gap_p99_ms')} ms, max {s.get('gap_max_ms')} ms, "
                                    f"but a radio simulator sets this cadence, so the figures "
                                    f"describe the simulation and the host rather than the PHY "
                                    f"[SCF225 delay management]")
        budget = float((ctx.knobs or {}).get("slot_jitter_budget_ms", 0.1))
        ok = (s.get("gap_p99_ms") or 0) <= budget
        return TestResult(self.id, self.name, "pass" if ok else "fail", metrics=s,
                          notes=f"p99 inter-arrival {s.get('gap_p99_ms')} ms against a "
                                f"{budget} ms budget [SCF225 delay management]")


class PnfTime02(RanTestCase):
    id, name, target = "PNF-TIME-02", "RACH.indication delivered inside the RAR window", "pnf"

    def run(self, ctx: RunContext):
        """How long the PHY took to report a preamble, measured in slots.

        This is the requirement that matters most on a bridged deployment: an indication that
        is correct but late cannot be answered, because the RAR must be scheduled inside
        ra-ResponseWindow and the window is counted from the preamble. A PHY that reports
        accurately but too slowly fails the UE exactly as a PHY that missed the preamble.

        Measured as the number of slot indications between the preamble's own slot and the
        indication arriving. Judged only when the deployment declares the window it configured,
        because the bar is the L2's configuration and not a constant.
        """
        o = fc.observation(ctx)
        nf, paths = o.get("nfapi"), (o.get("pcaps") or {})
        if nf is None or P7 not in paths:
            return rc._na(self.id, self.name, "no P7 capture to measure the delay from")  # noqa: SLF001
        msgs = nf.messages(paths[P7]) or []
        current, delays = None, []
        for m in msgs:
            if m.name == "SLOT.indication" and len(m.body) >= 4:
                current = (int.from_bytes(m.body[0:2], "big") * 20
                           + int.from_bytes(m.body[2:4], "big"))
            elif m.name == "RACH.indication" and len(m.body) >= 4 and current is not None:
                at = (int.from_bytes(m.body[0:2], "big") * 20
                      + int.from_bytes(m.body[2:4], "big"))
                delays.append((current - at + 1024 * 20) % (1024 * 20))
        if not delays:
            return rc._na(self.id, self.name,   # noqa: SLF001
                          "no RACH.indication was captured, so its delivery delay could not be "
                          "measured [SCF225 delay management / TS 38.321]")
        worst = max(delays)
        window = (ctx.knobs or {}).get("ra_response_window_slots")
        metrics = {"delays_slots": delays[:8], "worst_slots": worst,
                   "ra_response_window_slots": window}
        if window is None:
            return TestResult(self.id, self.name, "na", metrics=metrics,
                              notes=f"the PHY reported preambles {worst} slot(s) after the "
                                    f"occasion at worst, but the RAR window this deployment "
                                    f"configured was not declared, so there is no bar to judge "
                                    f"it against. Set knobs.ra_response_window_slots "
                                    f"[SCF225 delay management / TS 38.321]")
        ok = worst < int(window)
        return TestResult(self.id, self.name, "pass" if ok else "fail", metrics=metrics,
                          notes=f"worst RACH.indication delay {worst} slots against a "
                                f"{window}-slot RAR window "
                                f"[SCF225 delay management / TS 38.321]")


# --- capability integrity -----------------------------------------------------------
class PnfCap01(RanTestCase):
    id, name, target = "PNF-CAP-01", "PHY honours the capabilities it advertised", "pnf"

    def run(self, ctx: RunContext):
        """What the PHY claimed in PARAM.response, reported rather than assumed.

        Whether it *honours* those claims cannot be settled by a conformant stimulus: a VNF that
        stays inside the advertised set never tests the boundary, and driving the PHY outside it
        is a forged configuration, which is Level 2. So the advertised set is extracted and
        recorded, and this grades 'na' with that reason rather than passing a product that was
        never actually challenged.
        """
        o = fc.observation(ctx)
        nf, paths = o.get("nfapi"), (o.get("pcaps") or {})
        if nf is None or P5 not in paths:
            return rc._na(self.id, self.name, "no P5 capture to read PARAM.response from")  # noqa: SLF001
        body = b""
        for m in nf.messages(paths[P5]) or []:
            if m.name == "PARAM.response":
                body = m.body
                break
        if not body:
            return rc._na(self.id, self.name,   # noqa: SLF001
                          "PARAM.response carried no readable body, so the advertised "
                          f"capabilities could not be read [{SCF222} Table 3-9]")
        tlvs = _param_tlvs(body)
        pucch = tlvs.get(0x0011)
        return TestResult(self.id, self.name, "na",
                          metrics={"tlv_count": len(tlvs),
                                   "pucch_formats": pucch.hex() if pucch else None},
                          notes=f"the PHY advertised {len(tlvs)} capability TLVs"
                                + (f", PUCCH formats 0x{pucch.hex()}" if pucch else "")
                                + ". Whether it honours them can only be disproved by "
                                  "configuring it outside the advertised set, which is a forged "
                                  f"stimulus and belongs to Level 2 [{SCF222} Table 3-9]")


def _param_tlvs(body: bytes) -> dict[int, bytes]:
    """The tag/length/value list of a PARAM.response body (SCF222.10 Table 3-9).

    The body opens with an error code and a TLV count, then the TLVs themselves. Parsing stops
    at the first malformed entry rather than guessing, so a truncated capture yields fewer
    TLVs instead of invented ones.
    """
    out: dict[int, bytes] = {}
    if len(body) < 5:
        return out
    off = 5                                   # error_code(1) + number_of_tlvs(4)
    while off + 4 <= len(body):
        tag, length = (int.from_bytes(body[off:off + 2], "big"),
                       int.from_bytes(body[off + 2:off + 4], "big"))
        off += 4
        if length > len(body) - off:
            break
        out[tag] = body[off:off + length]
        off += (length + 3) & ~3              # TLVs are padded to a 4-byte boundary
    return out


# --- error handling, robustness and transport ----------------------------------------
class PnfErr01(RanTestCase):
    id, name, target = "PNF-ERR-01", "ERROR.indication on an invalid-state message", "pnf"
    def run(self, ctx):
        # A conformant VNF sends nothing invalid, so no error is the expected outcome and its
        # absence is 'not exercised' rather than a defect. Provoking one is Level 2.
        return fc.observed(ctx, self.id, self.name, P7, fc.PNF, "ERROR.indication",
                           f"{SCF222} Table 2-25", optional=True)


class PnfNeg01(RanTestCase):
    id, name, target = "PNF-NEG-01", "Malformed nFAPI datagram, no PNF crash", "pnf"
    def run(self, ctx):
        # P7 is UDP, so a malformed datagram reaches the product. The PNF exposes no P5
        # listener (it dials the VNF), so P7 is the only socket that can be probed.
        return rc.no_crash(ctx, self.id, self.name, "pnf", "udp", "SCF225 P7")


class PnfSec01(RanTestCase):
    id, name, target = "PNF-SEC-01", "nFAPI transport protection", "pnf"
    def run(self, ctx):
        return rc.transport_protected(ctx, self.id, self.name, "nFAPI P5 / P7",
                                      "SCF225 (defines no security for nFAPI)")


TESTS = [PnfP501, PnfP502, PnfP503, PnfP504, PnfP505, PnfP506, PnfP507, PnfP508, PnfP509,
         PnfP701, PnfP702, PnfP703, PnfP704, PnfP705, PnfP706, PnfP707, PnfP708, PnfP709,
         PnfSync01, PnfSync02, PnfTime01, PnfTime02, PnfCap01, PnfErr01, PnfNeg01, PnfSec01]
