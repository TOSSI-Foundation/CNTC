"""O-CU-CP (gNB-CU-CP) Level 1 test cases.

Driven by one real UE attach through the split gNB against a live core, and judged on the
NGAP, F1AP and E1AP captures the CU-CP writes itself. Because RRC crosses F1 inside F1AP
containers (TS 38.473 §8.4), the same F1AP decode supplies the RRC procedures and the evidence
that AS security actually activated.
"""
from __future__ import annotations

import time

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
        return rc.release_procedure(ctx, self.id, self.name, NGAP,
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
    id, name, target = "CUCP-NGAP-08", "NG association recovery after losing the AMF", "cucp"

    def run(self, ctx: RunContext):
        """The CU-CP must re-establish NG after the association to the AMF drops.

        The core is bring-your-own, so we do not restart it. Instead the SCTP path to the AMF is
        blocked at our own host with a firewall rule, held long enough for the association to
        fail, then released, an outage the CU-CP must recover from exactly as it would from an
        AMF restart. The rule is always removed again, including on error.
        """
        import subprocess
        amf = _amf_address(ctx)
        if not amf:
            return TestResult(self.id, self.name, "na",
                              notes="could not read the AMF address from the CU-CP config, so "
                                    "the association cannot be interrupted")
        rule = ["-p", "sctp", "-d", amf, "-j", "DROP"]

        def fw(action):
            return subprocess.run(["sudo", "-n", "iptables", action, "OUTPUT", *rule],
                                  capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=15)

        ran = ctx.ran
        if ran.node_alive("cucp") is not True:
            ran.start("cucp")
            if not ran.wait_for_log("cucp", "Connected to AMF", 45):
                return TestResult(self.id, self.name, "na",
                                  notes="the CU-CP never connected to the AMF, so there was no "
                                        "association to recover")
        if fw("-I").returncode != 0:
            return TestResult(self.id, self.name, "na",
                              notes="cannot install a firewall rule to interrupt N2 "
                                    "(needs passwordless sudo for iptables)")
        try:
            time.sleep(25)                     # outlive the SCTP heartbeat/retransmit window
        finally:
            fw("-D")
        recovered = ran.wait_for_log("cucp", "Connected to AMF", 90)
        alive = ran.node_alive("cucp")
        ok = bool(recovered) and alive is True
        ran.stop("cucp")
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"amf": amf, "reconnected": bool(recovered),
                                   "cucp_alive": alive},
                          notes=("the CU-CP re-established the NG association after the path to "
                                 "the AMF was restored " if ok else
                                 f"no NG re-establishment after the outage "
                                 f"(cucp_alive={alive}) ") + "[TS 38.413 §8.7.1 / TS 38.412]")


def _amf_address(ctx: RunContext) -> str:
    """The AMF the CU-CP is configured to reach, read from its own config."""
    try:
        cfg = ctx.ran._cfg_yaml("cucp")        # noqa: SLF001, adapter-specific by design
    except Exception:  # noqa: BLE001
        return ""
    addrs = ((cfg.get("cu_cp") or {}).get("amf") or {}).get("addrs")
    if isinstance(addrs, (list, tuple)):
        return str(addrs[0]) if addrs else ""
    return str(addrs or "")


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
        return rc.release_procedure(ctx, self.id, self.name, F1AP,
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
        # Deliberately NOT matching "Security mode command": on F1 that string is the *NAS* SMC
        # riding in a DL Information Transfer. The AS Security Mode Command is generated by the
        # CU-CP inside the UEContextSetupRequest's RRC container, so the observable pair is that
        # request plus the UE's RRC Security Mode Complete, which it only sends in response.
        return rc.detail(ctx, self.id, self.name, F1AP,
                         ["UEContextSetupRequest", "Security Mode Complete"],
                         "TS 38.331 §5.3.4")


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
        """RRC signalling must be ciphered once Access Stratum security is active.

        Judged on the ciphering algorithm the CU-CP selected, which it states on E1, and
        corroborated by whether the RRC layer still decodes after Security Mode Complete.
        Ciphered RRC is opaque, so readable message types are direct evidence it is in the clear.

        What this must NOT do is infer ciphering from the NAS message name disappearing. NAS runs
        its own security between the UE and the AMF, so NAS payloads go opaque after the NAS
        Security Mode procedure regardless of the Access Stratum. An earlier version of this case
        read exactly that and reported a PASS for RRC that was never ciphered.
        """
        o = rc.observation(ctx)
        a = o.get("as_security")
        r = o.get("rrc_ciphering")
        s = o.get("security_info")
        if not a or not a.get("smp"):
            return TestResult(self.id, self.name, "na",
                              notes="the attach never reached Security Mode Complete, so RRC "
                                    "ciphering cannot be judged")
        metrics = {k: v for k, v in (r or {}).items()}
        if s and s.get("seen"):
            metrics["ciphering"] = s["ciphering"]
            if s["ciphering_null"]:
                extra = (" (confirmed: RRC message types are still readable after Security Mode "
                         "Complete)") if (r or {}).get("rrc_readable_after_smc") else ""
                return TestResult(self.id, self.name, "fail", metrics=metrics,
                                  notes=f"the CU-CP selected {s['ciphering']}, so RRC signalling "
                                        f"is not ciphered{extra} [TS 33.511 §4.2.2.1.6]")
        if r is None:
            return TestResult(self.id, self.name, "na",
                              notes="no F1AP capture to judge RRC ciphering from")
        if r.get("rrc_readable_after_smc"):
            return TestResult(self.id, self.name, "fail", metrics=metrics,
                              notes=f"RRC message types are still readable after Security Mode "
                                    f"Complete (frames {r.get('readable_frames')}), so RRC is "
                                    f"not ciphered [TS 33.511 §4.2.2.1.6]")
        return TestResult(self.id, self.name, "pass", metrics=metrics,
                          notes="RRC is opaque after Security Mode Complete, so ciphering is "
                                "applied [TS 33.511 §4.2.2.1.6]")


class CucpSec03(RanTestCase):
    id, name, target = "CUCP-SEC-03", "AS algorithm selection (no NIA0/NEA0 downgrade)", "cucp"

    def run(self, ctx: RunContext):
        """The gNB must select a real algorithm, not a null one, when its policy calls for
        protection (TS 33.511 §4.2.2.1.12).

        The selected algorithms are read from the E1 Bearer Context Setup, which is where the
        CU-CP tells the CU-UP what it chose. A null algorithm is only acceptable if the
        corresponding protection is not required; selecting NEA0 while signalling
        confidentiality as "required" is self-contradictory and is reported as a failure.
        """
        o = rc.observation(ctx)
        s = o.get("security_info")
        if not s or not s.get("seen"):
            return TestResult(self.id, self.name, "na",
                              notes="no security IEs captured on E1, so the algorithm "
                                    "selection cannot be judged")
        conf_req = s["confidentiality_indication"] in ("required", "preferred")
        integ_req = s["integrity_indication"] in ("required", "preferred")
        problems = []
        if s["integrity_null"]:
            problems.append("NIA0 (null integrity) selected")
        if s["ciphering_null"] and conf_req:
            problems.append(f"NEA0 (null ciphering) selected while confidentiality is "
                            f"{s['confidentiality_indication']}")
        if problems:
            return TestResult(self.id, self.name, "fail", metrics=s,
                              notes="; ".join(problems) +
                                    f" (integrity={s['integrity']}, ciphering={s['ciphering']}) "
                                    f"[TS 33.511 §4.2.2.1.12]")
        note = f"selected integrity={s['integrity']}, ciphering={s['ciphering']}"
        if s["ciphering_null"] and not conf_req:
            note += ", NEA0 is permitted here because confidentiality is " \
                    f"{s['confidentiality_indication']}"
        return TestResult(self.id, self.name, "pass", metrics=s,
                          notes=note + " [TS 33.511 §4.2.2.1.12]")


class CucpSec04(RanTestCase):
    id, name, target = "CUCP-SEC-04", "UP security policy propagated to the CU-UP over E1", "cucp"

    def run(self, ctx: RunContext):
        """What the core asked for over N2 must be what the CU-CP relays over E1.

        The Security Indication is optional in NGAP (TS 38.413 §9.3.1.27). When the core omits
        it the CU-CP applies its own configured policy and there is nothing to compare, so the
        case cannot be judged, that is a property of the core, not a fault in the RAN.
        """
        o = rc.observation(ctx)
        e1 = o.get("security_info")
        ngap = o.get("ngap_security_indication")
        if not e1 or not e1.get("seen"):
            return TestResult(self.id, self.name, "na",
                              notes="no security IEs captured on E1 to compare against")
        if not ngap or not ngap.get("seen"):
            return TestResult(self.id, self.name, "na",
                              metrics={"e1": e1},
                              notes="the core sent no Security Indication over N2 (the IE is "
                                    "optional), so there is no signalled policy to propagate, "
                                    f"the CU-CP applied its own: confidentiality="
                                    f"{e1['confidentiality_indication']}, integrity="
                                    f"{e1['integrity_indication']} [TS 33.523 §5.2.2.1.4]")
        mismatches = [f"{k}: N2={ngap[k]} but E1={e1[k]}"
                      for k in ("confidentiality_indication", "integrity_indication")
                      if ngap[k] != e1[k]]
        if mismatches:
            return TestResult(self.id, self.name, "fail", metrics={"ngap": ngap, "e1": e1},
                              notes="the CU-CP did not relay the core's policy, " +
                                    "; ".join(mismatches) + " [TS 33.523 §5.2.2.1.4]")
        return TestResult(self.id, self.name, "pass", metrics={"ngap": ngap, "e1": e1},
                          notes=f"the core's policy (confidentiality="
                                f"{ngap['confidentiality_indication']}, integrity="
                                f"{ngap['integrity_indication']}) was relayed unchanged over E1 "
                                f"[TS 33.523 §5.2.2.1.4]")


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
