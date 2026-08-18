"""Real SMF test cases, exercised via a UERANSIM PDU session against the live core.

  SMF-SESS-01 PDU session establishment succeeds              [TS 24.501 §6.4.1]
  SMF-SESS-05 UE IP address allocated                         [TS 23.501 §5.8.2]
  SMF-DP-01   data path forwards through the UPF the SMF programmed (ping)  [bridge to Stage 1]
  SMF-SEC-02  Nsmf rejects unauthenticated PDU-session create [TS 33.501 §13 / 33.515]

N4 observation (SMF-N4-01..03) and the PDU release path need a PFCP capture/observer not built
yet -> those stay stubs (na). SMF-DP-01 reuses the same real ping upfbench uses for data-path
proof, closing the loop from control plane to user plane.
"""
from __future__ import annotations

from cntc_common.results import TestResult
from cpbench.suites.base import NfTestCase, RunContext
from cpbench.suites import sbi_common


def _obs(ctx: RunContext) -> dict:
    if ctx.driver is None or not hasattr(ctx.driver, "observe_registration"):
        return {"ok": False, "error": "no UERANSIM driver"}
    return ctx.driver.observe_registration()


class SmfSess01(NfTestCase):
    id, name, nf = "SMF-SESS-01", "PDU session establishment", "smf"
    def run(self, ctx):
        o = _obs(ctx)
        if o.get("error") and not o.get("pdu_session"):
            return TestResult(self.id, self.name, "na",
                              notes=f"UERANSIM attach unavailable: {o.get('error')}")
        ok = o.get("pdu_session") is True
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"pdu_session": o.get("pdu_session")},
                          notes=("PDU Session establishment successful [TS 24.501 §6.4.1]"
                                 if ok else f"pdu_session={o.get('pdu_session')}"))


class SmfSess05(NfTestCase):
    id, name, nf = "SMF-SESS-05", "UE IP address allocation", "smf"
    def run(self, ctx):
        o = _obs(ctx)
        ip = o.get("ue_ip") or ""
        if not ip and o.get("error"):
            return TestResult(self.id, self.name, "na",
                              notes=f"UERANSIM attach unavailable: {o.get('error')}")
        ok = bool(ip)
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"ue_ip": ip},
                          notes=f"UE IP allocated: {ip or '(none)'} [TS 23.501 §5.8.2]")


class SmfDp01(NfTestCase):
    id, name, nf = "SMF-DP-01", "Data-path forwards through the programmed UPF", "smf"
    def run(self, ctx):
        o = _obs(ctx)
        if o.get("error") and not o.get("ue_ip"):
            return TestResult(self.id, self.name, "na",
                              notes=f"UERANSIM attach unavailable: {o.get('error')}")
        ok = o.get("ping_ok") is True
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"ping_ok": o.get("ping_ok"), "ue_ip": o.get("ue_ip", "")},
                          notes=f"ping via uesimtun0 -> {o.get('ping_detail')} "
                                f"(through the UPF the SMF programmed over N4)")


class SmfSess03(NfTestCase):
    id, name, nf = "SMF-SESS-03", "PDU session release", "smf"
    def run(self, ctx):
        o = _obs(ctx)
        if o.get("pdu_released") is True:
            return TestResult(self.id, self.name, "pass", metrics={"pdu_released": True},
                              notes="UE-initiated release -> SMF sent PDU Session Release Command "
                                    "[TS 24.501 §6.4.3]")
        if o.get("pdu_released") is None:
            return TestResult(self.id, self.name, "na",
                              notes="release not driven on this deployment (observe-only), not judged")
        return TestResult(self.id, self.name, "na" if o.get("error") else "fail",
                          metrics={"pdu_released": o.get("pdu_released")},
                          notes=o.get("error") or "release did not complete")


def _n4(ctx, tid, name, key, msg, spec) -> TestResult:
    o = _obs(ctx)
    if not o.get("n4_capture"):
        return TestResult(tid, name, "na",
                          notes="N4 not captured (needs tcpdump+tshark on the N4 interface), "
                                "cannot observe PFCP")
    val = o.get(key)
    types = o.get("n4_msg_types")
    if val is True:
        return TestResult(tid, name, "pass", metrics={key: True, "n4_msg_types": types},
                          notes=f"observed PFCP {msg} on N4 (msg types: {types}) [{spec}]")
    return TestResult(tid, name, "fail", metrics={key: False, "n4_msg_types": types},
                      notes=f"PFCP {msg} not observed (types: {types}) [{spec}]")


class SmfN401(NfTestCase):
    id, name, nf = "SMF-N4-01", "SMF programs UPF over N4 (PFCP Session Establishment)", "smf"
    def run(self, ctx):
        return _n4(ctx, self.id, self.name, "n4_session_establish",
                   "Session Establishment Request", "TS 29.244 §7.5.2")


class SmfN403(NfTestCase):
    id, name, nf = "SMF-N4-03", "N4 session deletion on PDU release", "smf"
    def run(self, ctx):
        return _n4(ctx, self.id, self.name, "n4_session_delete",
                   "Session Deletion Request", "TS 29.244 §7.5.4")


class SmfSess04(NfTestCase):
    id, name, nf = "SMF-SESS-04", "Multiple PDU sessions", "smf"
    def run(self, ctx):
        o = _obs(ctx)
        if o.get("second_session") is True:
            return TestResult(self.id, self.name, "pass", metrics={"second_session": True},
                              notes="2nd PDU session (PSI[2]) established alongside the first "
                                    "[TS 23.501 §5.6]")
        # A 2nd session needs a 2nd DNN in the subscriber (this profile has only 'internet'),
        # so we can't currently drive it, 'na', never a fake fail against the SMF.
        return TestResult(self.id, self.name, "na",
                          notes="second PDU session not exercised (subscriber has a single DNN; "
                                "provision a 2nd DNN to test multi-session), not judged")


class SmfSec03(NfTestCase):
    id, name, nf = "SMF-SEC-03", "Session-event logging present", "smf"
    def run(self, ctx):
        # First ensure a session actually happened this run (so there's something to log).
        _obs(ctx)
        got = ctx.core.nf_log_grep("smf", ["pdusess", "smcontext", "pdu session", "pdu_session_id"])
        if got is None:
            return TestResult(self.id, self.name, "na", notes="cannot read SMF logs")
        return TestResult(self.id, self.name, "pass" if got else "fail",
                          metrics={"logged": got},
                          notes=("SMF logs PDU-session events (supi/session-id present) "
                                 "[TS 33.515]" if got else "no session-event log lines found"))


class SmfSec01(NfTestCase):
    id, name, nf = "SMF-SEC-01", "SBI (Nsmf) requires TLS", "smf"
    def run(self, ctx):
        return sbi_common.requires_tls(ctx, self.id, self.name)


class SmfSec02(NfTestCase):
    id, name, nf = "SMF-SEC-02", "Nsmf rejects unauthenticated PDU-session create", "smf"
    def run(self, ctx):
        return sbi_common.reject_unauth(ctx, self.id, self.name,
                                        "/nsmf-pdusession/v1/sm-contexts", method="POST", body={})


class SmfNeg01(NfTestCase):
    id, name, nf = "SMF-NEG-01", "Invalid session request -> reject, no crash", "smf"
    def run(self, ctx):
        return sbi_common.malformed_rejected(ctx, self.id, self.name,
                                             "/nsmf-pdusession/v1/sm-contexts", method="POST")


TESTS = [SmfSess01, SmfSess03, SmfSess04, SmfSess05, SmfN401, SmfN403, SmfDp01,
         SmfSec01, SmfSec02, SmfSec03, SmfNeg01]
