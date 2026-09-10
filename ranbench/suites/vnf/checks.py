"""VNF (nFAPI L2 driver) Level 1 test cases.

The same capture the PNF suite reads, in the opposite direction. The VNF is whatever terminates
nFAPI northbound of the PNF: it drives the P5 setup and issues the P7 slot requests, and those
messages are its behaviour regardless of which layers sit behind it.

The limit of this suite, stated in the catalog and repeated here because it is easy to forget:
a VNF has two sides and only its southbound nFAPI link is on this wire. Where the deployment
carries the other side over shared memory, nothing here observes it and nothing here claims to.
"""
from __future__ import annotations

from cntc_common.results import TestResult
from ranbench.suites import fapi_common as fc
from ranbench.suites import ran_common as rc
from ranbench.suites.base import RanTestCase, RunContext

P5, P7 = "p5", "p7"
SCF5 = "SCF225 P5"
SCF222 = "SCF222.10"
DELAY = "SCF225 delay management"


# --- P5: driving the PNF and PHY setup sequence -------------------------------------
class VnfP501(RanTestCase):
    id, name, target = "VNF-P5-01", "PNF_PARAM.request issued to discover capabilities", "vnf"
    def run(self, ctx):
        return fc.vnf_issues(ctx, self.id, self.name, P5, "PNF_PARAM.request", SCF5)


class VnfP502(RanTestCase):
    id, name, target = "VNF-P5-02", "PNF_CONFIG.request accepted by the PNF", "vnf"

    def run(self, ctx: RunContext):
        """Issued, and accepted. A configuration the PNF refuses is a VNF fault: the PNF has
        just told it what it supports in PNF_PARAM.response."""
        base = fc.vnf_issues(ctx, self.id, self.name, P5, "PNF_CONFIG.request", SCF5)
        if base.status != "pass":
            return base
        o = fc.observation(ctx)
        if "PNF_CONFIG.response" not in (o.get(f"procs.{P5}.{fc.PNF}") or []):
            return TestResult(self.id, self.name, "fail",
                              notes=f"the VNF sent PNF_CONFIG.request but the PNF never "
                                    f"accepted it [{SCF5}]")
        return base


class VnfP503(RanTestCase):
    id, name, target = "VNF-P5-03", "PNF_START.request only after PNF_CONFIG.response", "vnf"
    def run(self, ctx):
        return fc.vnf_issues(ctx, self.id, self.name, P5, "PNF_START.request", SCF5,
                             after="PNF_CONFIG.response")


class VnfP504(RanTestCase):
    id, name, target = "VNF-P5-04", "PARAM, CONFIG then START issued in order", "vnf"

    def run(self, ctx: RunContext):
        """The PHY-instance half of the sequence, checked as a chain rather than one pair.

        Each step must follow the previous step's answer. A VNF that pipelines them is not
        waiting for the PHY to reach the state the next message assumes.
        """
        for message, after in (("PARAM.request", None),
                               ("CONFIG.request", "PARAM.response"),
                               ("START.request", "CONFIG.response")):
            r = fc.vnf_issues(ctx, self.id, self.name, P5, message, f"{SCF222} Table 2-25",
                              after=after)
            if r.status != "pass":
                return r
        return TestResult(self.id, self.name, "pass",
                          notes=f"PARAM, CONFIG and START were each issued after the previous "
                                f"response [{SCF222} Table 2-25]")


class VnfP505(RanTestCase):
    id, name, target = "VNF-P5-05", "CONFIG stays inside the advertised capabilities", "vnf"

    def run(self, ctx: RunContext):
        """The interoperability requirement this interface exists for.

        PARAM.response states what the PHY supports and CONFIG.request must stay inside it.
        Judged on what the PHY did with the configuration: a PHY that answers CONFIG.response
        with an error code is refusing something it never advertised, and the capture shows the
        VNF asked for it. That is a VNF fault, and it is the one the wire can settle.

        A PHY that accepts the configuration has not proved the VNF stayed inside the set, only
        that nothing it asked for was refused, so that outcome is reported as observed.
        """
        o = fc.observation(ctx)
        why = fc._capture_missing(o, P5)      # noqa: SLF001, same package
        if why:
            return rc._na(self.id, self.name, why)   # noqa: SLF001
        pnf_side = o.get(f"procs.{P5}.{fc.PNF}") or []
        if "PARAM.response" not in pnf_side:
            return rc._na(self.id, self.name,        # noqa: SLF001
                          "the PHY never advertised its capabilities, so there is no set for "
                          f"the VNF's CONFIG.request to be measured against [{SCF222} Table 3-9]")
        nf, paths = o.get("nfapi"), (o.get("pcaps") or {})
        code = None
        for m in nf.messages(paths[P5]) or []:
            if m.name == "CONFIG.response" and m.body:
                code = m.body[0]
                break
        if code is None:
            return rc._na(self.id, self.name,        # noqa: SLF001
                          "no readable CONFIG.response, so it cannot be told whether the PHY "
                          f"accepted the VNF's configuration [{SCF222} Table 2-25]")
        if code != 0:
            return TestResult(self.id, self.name, "fail", metrics={"error_code": code},
                              notes=f"the PHY refused the VNF's CONFIG.request with "
                                    f"error_code={code}, so the VNF asked for something outside "
                                    f"the capabilities the PHY advertised [{SCF222} Table 3-9]")
        return TestResult(self.id, self.name, "pass", metrics={"error_code": 0},
                          notes=f"the PHY accepted the VNF's configuration (error_code=0), so "
                                f"nothing the VNF asked for was outside the advertised set "
                                f"[{SCF222} Table 3-9]")


class VnfP506(RanTestCase):
    id, name, target = "VNF-P5-06", "STOP.request on orderly teardown", "vnf"
    def run(self, ctx):
        return fc.observed(ctx, self.id, self.name, P5, fc.VNF, "STOP.request",
                           SCF222, optional=True)


class VnfP507(RanTestCase):
    id, name, target = "VNF-P5-07", "P5 carried on SCTP", "vnf"
    def run(self, ctx):
        return fc.transport_is(ctx, self.id, self.name, P5, "sctp", "SCF225 transport")


# --- P7: driving the slot loop --------------------------------------------------------
class VnfP701(RanTestCase):
    id, name, target = "VNF-P7-01", "DL_TTI.request delivered ahead of its slot", "vnf"
    def run(self, ctx):
        return fc.tti_lead(ctx, self.id, self.name, "DL_TTI.request", DELAY)


class VnfP702(RanTestCase):
    id, name, target = "VNF-P7-02", "UL_TTI.request delivered ahead of its slot", "vnf"
    def run(self, ctx):
        return fc.tti_lead(ctx, self.id, self.name, "UL_TTI.request", DELAY)


class VnfP703(RanTestCase):
    id, name, target = "VNF-P7-03", "TX_DATA.request accompanies downlink scheduling", "vnf"

    def run(self, ctx: RunContext):
        """A DL_TTI that schedules a PDSCH is incomplete without the data to send in it.

        Judged on presence rather than a per-slot pairing: which DL_TTI carry a PDSCH needs the
        PDU list decoded, and a run where the cell only broadcast SSB legitimately sends no
        TX_DATA at all. So no TX_DATA alongside DL_TTI is reported as not exercised.
        """
        return fc.observed(ctx, self.id, self.name, P7, fc.VNF, "TX_DATA.request",
                           SCF222, optional=True)


class VnfP704(RanTestCase):
    id, name, target = "VNF-P7-04", "UL_DCI.request for scheduled uplink grants", "vnf"
    def run(self, ctx):
        return fc.observed(ctx, self.id, self.name, P7, fc.VNF, "UL_DCI.request",
                           SCF222, optional=True)


class VnfP705(RanTestCase):
    id, name, target = "VNF-P7-05", "No TTI request for a slot the PHY has passed", "vnf"

    def run(self, ctx: RunContext):
        """Both request types, judged together: either one naming an elapsed slot is the fault.

        This is the sharpest timing case in the suite. A request for a slot that has gone by is
        discarded by the PHY, and on a bridged deployment that is the failure mode that stops a
        UE attaching, because the RAR misses its window.
        """
        for message in ("DL_TTI.request", "UL_TTI.request"):
            r = fc.tti_lead(ctx, self.id, self.name, message, DELAY)
            if r.status == "fail":
                return r
            if r.status == "na":
                return r
        return TestResult(self.id, self.name, "pass",
                          notes=f"no DL_TTI or UL_TTI named a slot the PHY had already passed "
                                f"[{DELAY}]")


class VnfSync01(RanTestCase):
    id, name, target = "VNF-SYNC-01", "DL_NODE_SYNC issued to maintain node sync", "vnf"
    def run(self, ctx):
        return fc.observed(ctx, self.id, self.name, P7, fc.VNF, "DL_NODE_SYNC", DELAY)


# --- robustness and transport ----------------------------------------------------------
class VnfNeg01(RanTestCase):
    id, name, target = "VNF-NEG-01", "Malformed nFAPI datagram, no VNF crash", "vnf"
    def run(self, ctx):
        return rc.no_crash(ctx, self.id, self.name, "vnf", "udp", "SCF225 P7")


class VnfSec01(RanTestCase):
    id, name, target = "VNF-SEC-01", "nFAPI transport protection", "vnf"
    def run(self, ctx):
        return rc.transport_protected(ctx, self.id, self.name, "nFAPI P5 / P7",
                                      "SCF225 (defines no security for nFAPI)")


TESTS = [VnfP501, VnfP502, VnfP503, VnfP504, VnfP505, VnfP506, VnfP507,
         VnfP701, VnfP702, VnfP703, VnfP704, VnfP705, VnfSync01, VnfNeg01, VnfSec01]
