"""O-CU-CP (gNB-CU-CP) Level 1 test cases.

Driven by one real UE attach through the split gNB against a live core, and judged on the
NGAP, F1AP and E1AP captures the CU-CP writes itself. Because RRC crosses F1 inside F1AP
containers (TS 38.473 §8.4), the same F1AP decode supplies the RRC procedures and the evidence
that AS security actually activated.
"""
from __future__ import annotations

from cntc_common.results import TestResult
from ranbench.suites import ran_common as rc
from ranbench.suites.base import RanTestCase, RunContext

NGAP = "cucp.ngap"
F1AP = "cucp.f1ap"
E1AP = "cucp.e1ap"


# --- NGAP / N2 (TS 38.413) -------------------------------------------------------
class CucpNgap01(RanTestCase):
    id, name, target = "CUCP-NGAP-01", "NG Setup, gNB-CU-CP ↔ AMF association", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, NGAP,
                             ["NGSetupRequest", "NGSetupResponse"], "TS 38.413 §8.7.1")


class CucpNgap02(RanTestCase):
    id, name, target = "CUCP-NGAP-02", "Initial UE Message carries the UE's NAS", "cucp"
    def run(self, ctx):
        return rc.detail(ctx, self.id, self.name, NGAP,
                         ["InitialUEMessage", "Registration request"], "TS 38.413 §8.6.1")


class CucpNgap03(RanTestCase):
    id, name, target = "CUCP-NGAP-03", "Initial Context Setup", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, NGAP,
                             ["InitialContextSetupRequest", "InitialContextSetupResponse"],
                             "TS 38.413 §8.3.1")


class CucpNgap04(RanTestCase):
    id, name, target = "CUCP-NGAP-04", "PDU Session Resource Setup with DL NG-U TNL", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, NGAP,
                             ["PDUSessionResourceSetupRequest", "PDUSessionResourceSetupResponse"],
                             "TS 38.413 §8.2.1")


class CucpNgap05(RanTestCase):
    id, name, target = "CUCP-NGAP-05", "UL / DL NAS transport", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, NGAP,
                             ["UplinkNASTransport", "DownlinkNASTransport"],
                             "TS 38.413 §8.6.2 / §8.6.3")


class CucpNgap06(RanTestCase):
    id, name, target = "CUCP-NGAP-06", "UE Context Release", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, NGAP,
                             ["UEContextReleaseCommand", "UEContextReleaseComplete"],
                             "TS 38.413 §8.3.3")


class CucpNgap07(RanTestCase):
    id, name, target = "CUCP-NGAP-07", "PDU Session Resource Release", "cucp"
    def run(self, ctx):
        # a compliant UE attach need not release the session before the context goes away
        return rc.procedures(ctx, self.id, self.name, NGAP,
                             ["PDUSessionResourceReleaseCommand", "PDUSessionResourceReleaseResponse"],
                             "TS 38.413 §8.2.2", optional=True)


class CucpNgap08(RanTestCase):
    id, name, target = "CUCP-NGAP-08", "NG association recovery after AMF restart", "cucp"
    def run(self, ctx: RunContext):
        return TestResult(self.id, self.name, "na",
                          notes="not exercised: recovery needs the AMF restarted mid-campaign, "
                                "which this stimulus does not do (the core is bring-your-own) "
                                "[TS 38.413 §8.7.1 / TS 38.412]")


# --- F1AP / F1-C, CU side (TS 38.473) --------------------------------------------
class CucpF101(RanTestCase):
    id, name, target = "CUCP-F1-01", "F1 Setup from the O-DU accepted", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["F1SetupRequest", "F1SetupResponse"], "TS 38.473 §8.2.3")


class CucpF102(RanTestCase):
    id, name, target = "CUCP-F1-02", "UE Context Setup toward the O-DU", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["UEContextSetupRequest", "UEContextSetupResponse"],
                             "TS 38.473 §8.3.1")


class CucpF103(RanTestCase):
    id, name, target = "CUCP-F1-03", "DL RRC Message Transfer delivers RRC", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["DLRRCMessageTransfer"], "TS 38.473 §8.4.2")


class CucpF104(RanTestCase):
    id, name, target = "CUCP-F1-04", "UE Context Modification", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["UEContextModificationRequest", "UEContextModificationResponse"],
                             "TS 38.473 §8.3.4", optional=True)


class CucpF105(RanTestCase):
    id, name, target = "CUCP-F1-05", "UE Context Release (CU-initiated)", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["UEContextReleaseCommand", "UEContextReleaseComplete"],
                             "TS 38.473 §8.3.3")


class CucpF106(RanTestCase):
    id, name, target = "CUCP-F1-06", "gNB-DU Configuration Update", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, F1AP,
                             ["GNBDUConfigurationUpdate", "GNBDUConfigurationUpdateAcknowledge"],
                             "TS 38.473 §8.2.4", optional=True)


# --- E1AP / E1, CP side (TS 38.463) ----------------------------------------------
class CucpE101(RanTestCase):
    id, name, target = "CUCP-E1-01", "E1 Setup from the O-CU-UP accepted", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, E1AP,
                             ["GNB-CU-UP-E1SetupRequest", "GNB-CU-UP-E1SetupResponse"],
                             "TS 38.463 §8.2.3")


class CucpE102(RanTestCase):
    id, name, target = "CUCP-E1-02", "Bearer Context Setup", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, E1AP,
                             ["BearerContextSetupRequest", "BearerContextSetupResponse"],
                             "TS 38.463 §8.3.1")


class CucpE103(RanTestCase):
    id, name, target = "CUCP-E1-03", "Bearer Context Modification (CP-initiated)", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, E1AP,
                             ["BearerContextModificationRequest", "BearerContextModificationResponse"],
                             "TS 38.463 §8.3.2", optional=True)


class CucpE104(RanTestCase):
    id, name, target = "CUCP-E1-04", "Bearer Context Release", "cucp"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, E1AP,
                             ["BearerContextReleaseCommand", "BearerContextReleaseComplete"],
                             "TS 38.463 §8.3.4", optional=True)


# --- RRC (TS 38.331), read out of the F1AP containers -----------------------------
class CucpRrc01(RanTestCase):
    id, name, target = "CUCP-RRC-01", "RRC Setup / RRC Setup Complete", "cucp"
    def run(self, ctx):
        return rc.detail(ctx, self.id, self.name, F1AP,
                         ["RRC Setup", "RRC Setup Complete"], "TS 38.331 §5.3.3")


class CucpRrc02(RanTestCase):
    id, name, target = "CUCP-RRC-02", "AS Security Mode Command / Complete", "cucp"
    def run(self, ctx):
        return rc.detail(ctx, self.id, self.name, F1AP,
                         ["Security mode command", "Security Mode Complete"], "TS 38.331 §5.3.4")


class CucpRrc03(RanTestCase):
    id, name, target = "CUCP-RRC-03", "RRC Reconfiguration establishing the DRB", "cucp"
    def run(self, ctx):
        return rc.detail(ctx, self.id, self.name, F1AP,
                         ["RRC Reconfiguration"], "TS 38.331 §5.3.5")


class CucpRrc04(RanTestCase):
    id, name, target = "CUCP-RRC-04", "RRC Release", "cucp"
    def run(self, ctx):
        return rc.detail(ctx, self.id, self.name, F1AP, ["RRC Release"],
                         "TS 38.331 §5.3.8", optional=True)


# --- Security assurance (TS 33.523 §5.2.2.1 / TS 33.511 §4.2.2.1) -----------------
class CucpSec01(RanTestCase):
    id, name, target = "CUCP-SEC-01", "RRC integrity protection after AS SMC", "cucp"
    def run(self, ctx: RunContext):
        o = rc.observation(ctx)
        s = o.get("as_security")
        if not s:
            return TestResult(self.id, self.name, "na",
                              notes="no F1AP capture to judge AS security from")
        if not s.get("smp"):
            return TestResult(self.id, self.name, "na",
                              notes="the attach never reached Security Mode Complete, so "
                                    "post-activation protection cannot be judged")
        if s["integrity_activated"]:
            return TestResult(self.id, self.name, "pass", metrics=s,
                              notes=f"RRC unprotected before AS SMC (MAC {s['pre_smc_macs']}) "
                                    f"and integrity-protected after it (MAC {s['post_smc_macs']}) "
                                    f"[TS 33.511 §4.2.2.1.1]")
        return TestResult(self.id, self.name, "fail", metrics=s,
                          notes=f"no integrity-protected RRC observed after AS SMC "
                                f"(post-SMC MACs {s['post_smc_macs']}) [TS 33.511 §4.2.2.1.1]")


class CucpSec02(RanTestCase):
    id, name, target = "CUCP-SEC-02", "RRC ciphering after AS SMC", "cucp"
    def run(self, ctx: RunContext):
        o = rc.observation(ctx)
        s = o.get("as_security")
        if not s or not s.get("smp"):
            return TestResult(self.id, self.name, "na",
                              notes="no post-SMC RRC captured, so ciphering cannot be judged")
        if s["ciphered"]:
            return TestResult(self.id, self.name, "pass", metrics=s,
                              notes="RRC containers after AS SMC no longer expose their inner "
                                    "NAS message, ciphering applied [TS 33.511 §4.2.2.1.6]")
        return TestResult(self.id, self.name, "fail", metrics=s,
                          notes="RRC after AS SMC still renders its plaintext content, not "
                                "ciphered [TS 33.511 §4.2.2.1.6]")


class CucpSec03(RanTestCase):
    id, name, target = "CUCP-SEC-03", "AS algorithm selection (no NIA0/NEA0 downgrade)", "cucp"
    def run(self, ctx: RunContext):
        o = rc.observation(ctx)
        a = o.get("as_algorithms")
        if not a or not a.get("seen"):
            return TestResult(self.id, self.name, "na",
                              notes="the Security Mode Command's algorithm IEs were not "
                                    "decodable, so the selection cannot be judged")
        null = {"0", "nea0", "nia0"}
        ciph, integ = str(a.get("ciphering", "")), str(a.get("integrity", ""))
        if integ.lower() in null:
            return TestResult(self.id, self.name, "fail", metrics=a,
                              notes=f"NIA0 (null integrity) selected [TS 33.511 §4.2.2.1.12]")
        return TestResult(self.id, self.name, "pass", metrics=a,
                          notes=f"AS algorithms selected: ciphering={ciph}, integrity={integ} "
                                f"(non-null integrity) [TS 33.511 §4.2.2.1.12]")


class CucpSec04(RanTestCase):
    id, name, target = "CUCP-SEC-04", "UP security policy propagated to the CU-UP over E1", "cucp"
    def run(self, ctx: RunContext):
        return TestResult(self.id, self.name, "na",
                          notes="not implemented: needs the SMF's security indication IEs "
                                "extracted from NGAP and compared with the E1 Bearer Context "
                                "Setup [TS 33.523 §5.2.2.1.4]")


class CucpSec05(RanTestCase):
    id, name, target = "CUCP-SEC-05", "F1-C / E1 transport protection", "cucp"
    def run(self, ctx):
        return rc.transport_protected(ctx, self.id, self.name, "F1-C / E1",
                                      "TS 33.523 §5.2.2.1.2 / §5.2.2.1.3")


class CucpSec06(RanTestCase):
    id, name, target = "CUCP-SEC-06", "N2 transport protection", "cucp"
    def run(self, ctx):
        return rc.transport_protected(ctx, self.id, self.name, "N2",
                                      "TS 33.511 §4.2.2.1.16 / §4.2.2.1.17")


# --- Robustness -------------------------------------------------------------------
class CucpNeg01(RanTestCase):
    id, name, target = "CUCP-NEG-01", "Malformed input on the F1-C / E1 listener", "cucp"
    def run(self, ctx):
        return rc.no_crash(ctx, self.id, self.name, "cucp", "sctp", "TS 38.473 §8.2.2")


class CucpNeg02(RanTestCase):
    id, name, target = "CUCP-NEG-02", "Unknown F1AP procedure → Error Indication", "cucp"
    def run(self, ctx: RunContext):
        return TestResult(self.id, self.name, "na",
                          notes="not implemented: needs an F1AP encoder to emit a well-formed "
                                "PDU with an unsupported procedure code [TS 38.473 §8.2.2]")


TESTS = [CucpNgap01, CucpNgap02, CucpNgap03, CucpNgap04, CucpNgap05, CucpNgap06, CucpNgap07,
         CucpNgap08, CucpF101, CucpF102, CucpF103, CucpF104, CucpF105, CucpF106,
         CucpE101, CucpE102, CucpE103, CucpE104,
         CucpRrc01, CucpRrc02, CucpRrc03, CucpRrc04,
         CucpSec01, CucpSec02, CucpSec03, CucpSec04, CucpSec05, CucpSec06,
         CucpNeg01, CucpNeg02]
