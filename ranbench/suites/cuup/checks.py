"""O-CU-UP (gNB-CU-UP) Level 1 test cases.

Judged from the E1 signalling it exchanges with the CU-CP and from the two GTP-U interfaces it
owns, F1-U toward the O-DU and N3 toward the UPF. Like the O-DU it is an SCTP client (on E1),
so its probeable socket is GTP-U.

The security cases here are the ones TS 33.523 clause 6 makes specific to this product class:
the CU-UP hosts PDCP for the user plane, so it is what enforces the ciphering and integrity
policy the SMF signalled and the CU-CP relayed over E1.
"""
from __future__ import annotations

from cntc_common.results import TestResult
from ranbench.suites import ran_common as rc
from ranbench.suites.base import RanTestCase, RunContext

E1AP = "cuup.e1ap"


class CuupE101(RanTestCase):
    id, name, target = "CUUP-E1-01", "E1 Setup advertising supported PLMNs / slices", "cuup"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, E1AP,
                             ["GNB-CU-UP-E1SetupRequest", "GNB-CU-UP-E1SetupResponse"],
                             "TS 38.463 §8.2.3")


class CuupE102(RanTestCase):
    id, name, target = "CUUP-E1-02", "Bearer Context Setup allocates F1-U and NG-U TNL", "cuup"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, E1AP,
                             ["BearerContextSetupRequest", "BearerContextSetupResponse"],
                             "TS 38.463 §8.3.1")


class CuupE103(RanTestCase):
    id, name, target = "CUUP-E1-03", "Bearer Context Modification applies the DL F1-U TNL", "cuup"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, E1AP,
                             ["BearerContextModificationRequest", "BearerContextModificationResponse"],
                             "TS 38.463 §8.3.2", optional=True)


class CuupE104(RanTestCase):
    id, name, target = "CUUP-E1-04", "Bearer Context Release frees the TEIDs", "cuup"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, E1AP,
                             ["BearerContextReleaseCommand", "BearerContextReleaseComplete"],
                             "TS 38.463 §8.3.4", optional=True)


class CuupE105(RanTestCase):
    id, name, target = "CUUP-E1-05", "gNB-CU-UP Configuration Update", "cuup"
    def run(self, ctx):
        return rc.procedures(ctx, self.id, self.name, E1AP,
                             ["GNB-CU-UP-ConfigurationUpdate", "GNB-CU-UP-ConfigurationUpdateAcknowledge"],
                             "TS 38.463 §8.2.5", optional=True)


class CuupUp01(RanTestCase):
    id, name, target = "CUUP-UP-01", "F1-U tunnel to the O-DU on the signalled TEID", "cuup"
    def run(self, ctx):
        return rc.gtpu_traffic(ctx, self.id, self.name, "cuup.f1u", "TS 38.425 / TS 29.281")


class CuupUp02(RanTestCase):
    id, name, target = "CUUP-UP-02", "N3 tunnel to the UPF with QFI marking", "cuup"
    def run(self, ctx):
        return rc.gtpu_traffic(ctx, self.id, self.name, "cuup.n3",
                               "TS 38.415 / TS 29.281", need_qfi=True)


class CuupUp03(RanTestCase):
    id, name, target = "CUUP-UP-03", "End-to-end data path to the data network", "cuup"
    def run(self, ctx: RunContext):
        o = rc.observation(ctx)
        if o.get("error"):
            return TestResult(self.id, self.name, "na",
                              notes=f"attach unavailable: {o['error']}")
        ok = o.get("ping_ok")
        if ok is None:
            return TestResult(self.id, self.name, "na",
                              notes="the UE never got a PDU session, so no data path could be "
                                    "exercised")
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"ue_ip": o.get("ue_ip", ""), "ping": o.get("ping_detail", "")},
                          notes=f"UE {o.get('ue_ip','?')} via {o.get('tun','?')}: "
                                f"{o.get('ping_detail','')} [TS 23.501 §5.8]")


class CuupUp04(RanTestCase):
    id, name, target = "CUUP-UP-04", "Unknown TEID dropped, no crash", "cuup"
    def run(self, ctx):
        return rc.no_crash(ctx, self.id, self.name, "cuup", "udp", "TS 29.281",
                           needs=("cucp",))


class CuupSec01(RanTestCase):
    id, name, target = "CUUP-SEC-01", "UP ciphering per the E1 security policy", "cuup"

    def run(self, ctx: RunContext):
        """The user plane must be ciphered when the signalled policy requires it.

        Judged on the E1 Bearer Context Setup, which states both the protection the policy
        requires and the algorithm actually selected. That pair is unambiguous.

        Do NOT try to infer this from whether the payload looks opaque on the wire. F1-U carries
        PDCP PDUs (TS 38.425), so no inner IP header is ever visible there regardless of
        ciphering, and N3 carries the UE's plain IP by design once PDCP has been terminated.
        An earlier version of this case read "no readable IP on F1-U" as "ciphering applied" and
        issued a PASS for a user plane that was running with the null algorithm.
        """
        o = rc.observation(ctx)
        if o.get("error"):
            return TestResult(self.id, self.name, "na", notes=f"attach unavailable: {o['error']}")
        s = o.get("security_info")
        if not s or not s.get("seen"):
            return TestResult(self.id, self.name, "na",
                              notes="no security IEs captured on E1, so the user-plane "
                                    "ciphering policy cannot be judged")
        required = s["confidentiality_indication"] in ("required", "preferred")
        metrics = dict(s)
        if s["ciphering_null"] and required:
            return TestResult(self.id, self.name, "fail", metrics=metrics,
                              notes=f"confidentiality is signalled as "
                                    f"{s['confidentiality_indication']} but the CU-UP was given "
                                    f"{s['ciphering']}, the user plane is not ciphered "
                                    f"[TS 33.523 §6.2.2.1.7]")
        if s["ciphering_null"]:
            return TestResult(self.id, self.name, "na", metrics=metrics,
                              notes=f"ciphering is {s['ciphering']} and confidentiality is "
                                    f"{s['confidentiality_indication']}, so no ciphering was "
                                    f"required of the CU-UP, nothing to enforce "
                                    f"[TS 33.523 §6.2.2.1.7]")
        return TestResult(self.id, self.name, "pass", metrics=metrics,
                          notes=f"the CU-UP was given {s['ciphering']} with confidentiality "
                                f"{s['confidentiality_indication']} [TS 33.523 §6.2.2.1.7]")


class CuupSec02(RanTestCase):
    id, name, target = "CUUP-SEC-02", "UP integrity when the policy requires it", "cuup"

    def run(self, ctx: RunContext):
        """Integrity protection of user data is conditional on the signalled policy
        (TS 33.523 §6.2.2.1.6), so the requirement only bites when it is asked for."""
        o = rc.observation(ctx)
        s = o.get("security_info")
        if not s or not s.get("seen"):
            return TestResult(self.id, self.name, "na",
                              notes="no security IEs captured on E1 to judge the integrity "
                                    "policy from")
        required = s["integrity_indication"] in ("required", "preferred")
        if not required:
            return TestResult(self.id, self.name, "na", metrics=s,
                              notes=f"user-plane integrity is signalled as "
                                    f"{s['integrity_indication']}, so none was required of the "
                                    f"CU-UP, nothing to enforce [TS 33.523 §6.2.2.1.6]")
        if s["integrity_null"]:
            return TestResult(self.id, self.name, "fail", metrics=s,
                              notes=f"integrity is {s['integrity_indication']} but the CU-UP was "
                                    f"given {s['integrity']} [TS 33.523 §6.2.2.1.6]")
        return TestResult(self.id, self.name, "pass", metrics=s,
                          notes=f"user-plane integrity {s['integrity']} applied as "
                                f"{s['integrity_indication']} [TS 33.523 §6.2.2.1.6]")


class CuupSec03(RanTestCase):
    id, name, target = "CUUP-SEC-03", "E1 transport protection", "cuup"
    def run(self, ctx):
        return rc.transport_protected(ctx, self.id, self.name, "E1",
                                      "TS 33.523 §6.2.2.1.2 / §6.2.2.1.3")


class CuupSec04(RanTestCase):
    id, name, target = "CUUP-SEC-04", "F1-U / N3 transport protection", "cuup"
    def run(self, ctx):
        return rc.transport_protected(ctx, self.id, self.name, "F1-U / N3",
                                      "TS 33.523 §6.2.2.1.4 / §6.2.2.1.5")


class CuupNeg01(RanTestCase):
    id, name, target = "CUUP-NEG-01", "Malformed GTP-U → no O-CU-UP crash", "cuup"
    def run(self, ctx):
        return rc.no_crash(ctx, self.id, self.name, "cuup", "udp", "TS 29.281",
                           needs=("cucp",))


TESTS = [CuupE101, CuupE102, CuupE103, CuupE104, CuupE105,
         CuupUp01, CuupUp02, CuupUp03, CuupUp04,
         CuupSec01, CuupSec02, CuupSec03, CuupSec04, CuupNeg01]
