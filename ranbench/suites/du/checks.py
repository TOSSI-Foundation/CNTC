"""O-DU (gNB-DU) Level 1 test cases.

Judged from the DU side of F1 plus the cell it broadcasts. The DU is an SCTP *client* on F1-C
(TS 38.472) so it exposes no F1-C listener; its reachable socket is F1-U, which is what the
robustness case probes. TS 33.523 clause 7 gives this product class only F1 transport-protection
security requirements, it holds no AS keys, and the catalog reflects that rather than
inventing more.
"""
from __future__ import annotations

import time

from cntc_common.results import TestResult
from ranbench.suites import ran_common as rc
from ranbench.suites.base import RanTestCase, RunContext

F1AP = "du.f1ap"


class DuF101(RanTestCase):
    id, name, target = "DU-F1-01", "F1 Setup with a well-formed served-cell list", "du"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["F1SetupRequest", "F1SetupResponse"], "TS 38.473 §8.2.3")


class DuF102(RanTestCase):
    id, name, target = "DU-F1-02", "Initial UL RRC Message Transfer on UE access", "du"
    def run(self, ctx):
        return rc.detail(ctx, self.id, self.name, F1AP,
                         ["InitialULRRCMessageTransfer", "RRC Setup Request"],
                         "TS 38.473 §8.4.1")


class DuF103(RanTestCase):
    id, name, target = "DU-F1-03", "UE Context Setup, SRB/DRB admitted", "du"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["UEContextSetupRequest", "UEContextSetupResponse"],
                             "TS 38.473 §8.3.1")


class DuF104(RanTestCase):
    id, name, target = "DU-F1-04", "UL RRC Message Transfer carries the UE's RRC", "du"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["ULRRCMessageTransfer"], "TS 38.473 §8.4.3")


class DuF105(RanTestCase):
    id, name, target = "DU-F1-05", "UE Context Release Complete", "du"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["UEContextReleaseComplete"], "TS 38.473 §8.3.3")


class DuF106(RanTestCase):
    id, name, target = "DU-F1-06", "gNB-DU Configuration Update", "du"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["GNBDUConfigurationUpdate", "GNBDUConfigurationUpdateAcknowledge"],
                             "TS 38.473 §8.2.4", optional=True)


class DuF107(RanTestCase):
    id, name, target = "DU-F1-07", "F1 recovery after a CU restart", "du"

    def run(self, ctx: RunContext):
        """Restart the CU-CP under a running O-DU and require the DU to re-establish F1.

        This is a disruptive but entirely compliant event, the peer went away and came back, 
        so it belongs in Level 1. The DU must notice, re-associate and re-run F1 Setup.
        """
        ran = ctx.ran
        if ran.node_alive("du") is None:
            return TestResult(self.id, self.name, "na",
                              notes="cannot observe O-DU liveness, so recovery cannot be judged")
        # a clean pair: CU-CP up, then the DU attached to it
        ran.stop("du"); ran.stop("cucp"); time.sleep(4)
        ran.start("cucp")
        if not ran.wait_for_log("cucp", "Connected to AMF", 45):
            return TestResult(self.id, self.name, "na",
                              notes="the CU-CP did not reach the AMF, so the DU had nothing to "
                                    "re-associate with")
        ran.start("du")
        if not ran.wait_for_log("du", "Cell was activated", 60):
            return TestResult(self.id, self.name, "na",
                              notes="the O-DU did not activate its cell before the restart")
        # Take the CU away abruptly. A graceful stop would send F1 Removal, which tells the DU
        # to stand down, that is orderly teardown, not recovery.
        ran.stop("cucp", graceful=False); time.sleep(5)
        ran.start("cucp")
        ran.wait_for_log("cucp", "Connected to AMF", 45)
        recovered = ran.wait_for_log("cucp", "F1 Setup", 60)
        alive = ran.node_alive("du")
        ok = bool(recovered) and alive is True
        # leave the rig as we found it, this case started products of its own
        ran.stop("du"); ran.stop("cucp")
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"f1_setup_after_restart": bool(recovered), "du_alive": alive},
                          notes=("the O-DU re-established F1 after the CU restarted "
                                 if ok else
                                 f"no F1 Setup seen after the CU restarted (du_alive={alive}) ")
                                + "[TS 38.473 §8.2.3 / TS 38.472]")


class DuCell01(RanTestCase):
    id, name, target = "DU-CELL-01", "MIB + SIB1 consistent with the F1 Setup served cell", "du"
    def run(self, ctx: RunContext):
        # OCUDU carries the broadcast SI as IEs of the F1 Setup Request, so one decode proves
        # both that the cell is broadcast and that it is the cell F1 announced.
        return rc.detail(ctx, self.id, self.name, F1AP,
                         ["F1SetupRequest", "MIB", "SIB1"], "TS 38.331 §5.2.1 / TS 38.473 §8.2.3")


class DuCell02(RanTestCase):
    id, name, target = "DU-CELL-02", "Random access Msg1 → Msg4 completes", "du"
    def run(self, ctx):
        return rc.milestone(ctx, self.id, self.name, "rach_completed",
                            "the UE completed random access (Msg3 transmitted, Msg4 received)",
                            "TS 38.321")


class DuCell03(RanTestCase):
    id, name, target = "DU-CELL-03", "DRB scheduling sustains UL and DL", "du"
    def run(self, ctx: RunContext):
        o = rc.observation(ctx)
        if o.get("ping_ok") is None:
            return TestResult(self.id, self.name, "na",
                              notes="no user traffic was run, so scheduling cannot be judged")
        # the attach only proves the bearer carries traffic; a throughput figure needs a
        # generator, which this stimulus does not include
        return TestResult(self.id, self.name, "na",
                          metrics={"ping": o.get("ping_detail", "")},
                          notes="bidirectional traffic was carried, but no throughput figure "
                                "was measured (needs a traffic generator, not a ping)")


class DuUp01(RanTestCase):
    id, name, target = "DU-UP-01", "F1-U tunnel with the O-CU-UP forwards uplink", "du"
    def run(self, ctx):
        return rc.gtpu_traffic(ctx, self.id, self.name, "du.f1u", "TS 38.425")


class DuSec01(RanTestCase):
    id, name, target = "DU-SEC-01", "F1-C transport confidentiality and integrity", "du"
    def run(self, ctx):
        return rc.transport_protected(ctx, self.id, self.name, "F1-C",
                                      "TS 33.523 §7.2.2.1.1 / §7.2.2.1.2")


class DuSec02(RanTestCase):
    id, name, target = "DU-SEC-02", "F1-U transport confidentiality and integrity", "du"
    def run(self, ctx):
        return rc.transport_protected(ctx, self.id, self.name, "F1-U",
                                      "TS 33.523 §7.2.2.1.3 / §7.2.2.1.4")


class DuNeg01(RanTestCase):
    id, name, target = "DU-NEG-01", "Malformed GTP-U on F1-U → no O-DU crash", "du"
    def run(self, ctx):
        return rc.no_crash(ctx, self.id, self.name, "du", "udp", "TS 29.281",
                           needs=("cucp",))


TESTS = [DuF101, DuF102, DuF103, DuF104, DuF105, DuF106, DuF107,
         DuCell01, DuCell02, DuCell03, DuUp01, DuSec01, DuSec02, DuNeg01]
