"""Real AMF test cases — driven over N1/N2 by UERANSIM against the live core.

Anchored to the specs and asserted on the observed NG/NAS procedure outcomes:
  AMF-NGAP-01 NG Setup succeeds (gNB↔AMF association)        [TS 38.413 §8.7]
  AMF-REG-01  initial registration completes                 [TS 24.501 §5.5.1.2]
  AMF-AUTH-01 5G-AKA authentication runs                      [TS 33.501 §6.1.3.2]
  AMF-AUTH-02 security mode command/complete                  [TS 24.501 §5.4.2]
  AMF-SEC-07  SBI (Namf) rejects unauthenticated requests     [TS 33.501 §13]

The wire-capture SCAS cases (NAS ciphering/integrity/replay, RES* bypass) and the malformed-
NGAP robustness case need a packet crafter/observer not built yet, so they stay stubs -> na,
never a silent pass (see docs/PLAN-CONTROL-PLANE.md §3, §8).
"""
from __future__ import annotations

import socket
import time

from cntc_common.results import TestResult
from cpbench.suites.base import NfTestCase, RunContext
from cpbench.suites import sbi_common


def _sctp_garbage(addr: str, port: int = 38412, timeout: float = 4.0) -> tuple[bool, str]:
    """Open an SCTP association to the AMF's N2 port and send bytes that are NOT a valid
    NGAP PDU. Returns (sent, detail); a connection reset is fine — we judge by AMF liveness."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 132)  # IPPROTO_SCTP
        s.settimeout(timeout)
        s.connect((addr, port))
        s.send(b"\xde\xad\xbe\xef" * 64)
        s.close()
        return True, "garbage NGAP bytes sent over SCTP"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _obs(ctx: RunContext) -> dict:
    if ctx.driver is None or not hasattr(ctx.driver, "observe_registration"):
        return {"ok": False, "error": "no UERANSIM driver"}
    return ctx.driver.observe_registration()


def _from_obs(ctx, tid, name, key, ok_note, spec) -> TestResult:
    o = _obs(ctx)
    if o.get("error") and o.get(key) is None:
        return TestResult(tid, name, "na", notes=f"UERANSIM attach unavailable: {o.get('error')}")
    val = o.get(key)
    if val is True:
        return TestResult(tid, name, "pass", metrics={key: True, "ue_ip": o.get("ue_ip", "")},
                          notes=f"{ok_note} [{spec}]")
    if val is None:
        # not driven on this deployment (e.g. observe-only k8s) -> 'na', never a fake fail
        return TestResult(tid, name, "na",
                          notes=f"{key} not exercised on this deployment (observe-only) [{spec}]")
    return TestResult(tid, name, "fail", metrics={key: val},
                      notes=f"observed {key}={val} (expected success) [{spec}]")


class AmfNgap01(NfTestCase):
    id, name, nf = "AMF-NGAP-01", "NG Setup — gNB↔AMF association", "amf"
    def run(self, ctx):  # noqa: D102
        return _from_obs(ctx, self.id, self.name, "ng_setup",
                         "NG Setup procedure successful", "TS 38.413 §8.7")


class AmfReg01(NfTestCase):
    id, name, nf = "AMF-REG-01", "Initial registration", "amf"
    def run(self, ctx):
        return _from_obs(ctx, self.id, self.name, "registered",
                         "Initial Registration successful", "TS 24.501 §5.5.1.2")


class AmfAuth01(NfTestCase):
    id, name, nf = "AMF-AUTH-01", "5G-AKA authentication", "amf"
    def run(self, ctx):
        return _from_obs(ctx, self.id, self.name, "auth_request",
                         "Authentication Request issued and registration completed",
                         "TS 33.501 §6.1.3.2")


class AmfAuth02(NfTestCase):
    id, name, nf = "AMF-AUTH-02", "Security-mode command / complete", "amf"
    def run(self, ctx):
        return _from_obs(ctx, self.id, self.name, "security_mode",
                         "Security Mode Command received and completed", "TS 24.501 §5.4.2")


def _nas_security(ctx, tid, name, key, prop, spec) -> TestResult:
    o = _obs(ctx)
    if not o.get("nas_capture"):
        return TestResult(tid, name, "na",
                          notes="N2 not captured (needs tcpdump+tshark on the N2 interface; "
                                "set drivers.n2_iface) — cannot judge NAS protection")
    val = o.get(key)
    types = o.get("nas_sec_types")
    if val is True:
        return TestResult(tid, name, "pass", metrics={key: True, "nas_sec_types": types},
                          notes=f"post-security NAS messages are {prop} "
                                f"(security header types seen: {types}) [{spec}]")
    return TestResult(tid, name, "fail", metrics={key: False, "nas_sec_types": types},
                      notes=f"no {prop} NAS messages observed (types: {types}) [{spec}]")


class AmfSec01(NfTestCase):
    id, name, nf = "AMF-SEC-01", "NAS integrity protection after security activation", "amf"
    def run(self, ctx):
        return _nas_security(ctx, self.id, self.name, "nas_integrity",
                             "integrity-protected", "TS 33.512 / TS 24.501 §4.4.2")


class AmfSec02(NfTestCase):
    id, name, nf = "AMF-SEC-02", "NAS confidentiality (ciphering) after SMC", "amf"
    def run(self, ctx):
        return _nas_security(ctx, self.id, self.name, "nas_ciphered",
                             "ciphered", "TS 33.512 / TS 24.501 §4.4.5")


class AmfNgap02(NfTestCase):
    id, name, nf = "AMF-NGAP-02", "Initial UE message / NAS transport", "amf"
    def run(self, ctx):
        return _from_obs(ctx, self.id, self.name, "initial_nas_message",
                         "Initial UE Message / NAS transport carried the UE's NAS over N2",
                         "TS 38.413 §8.6")


class AmfNgap03(NfTestCase):
    id, name, nf = "AMF-NGAP-03", "Initial context setup", "amf"
    def run(self, ctx):
        return _from_obs(ctx, self.id, self.name, "initial_context_setup",
                         "AMF sent Initial Context Setup Request (observed on gNB)",
                         "TS 38.413 §8.3")


class AmfNgap04(NfTestCase):
    id, name, nf = "AMF-NGAP-04", "UE context release", "amf"
    def run(self, ctx):
        return _from_obs(ctx, self.id, self.name, "ue_context_release",
                         "AMF sent UE Context Release Command (observed on gNB)",
                         "TS 38.413 §8.3")


class AmfDereg01(NfTestCase):
    id, name, nf = "AMF-DEREG-01", "UE-initiated deregistration", "amf"
    def run(self, ctx):
        return _from_obs(ctx, self.id, self.name, "deregistered",
                         "UE-initiated de-registration successful", "TS 24.501 §5.5.2.2")


class AmfNeg01(NfTestCase):
    id, name, nf = "AMF-NEG-01", "Malformed NGAP PDU -> reject, no AMF crash", "amf"
    def run(self, ctx: RunContext):
        amf = ctx.cfg.drivers.get("amf_n2_addr", "") or (ctx.endpoint.split(":")[0] if ctx.endpoint else "")
        port = int(ctx.cfg.drivers.get("amf_n2_port", 38412))   # NodePort on k8s (e.g. 31412)
        if not amf:
            return TestResult(self.id, self.name, "na", notes="no AMF N2 address (set drivers.amf_n2_addr)")
        alive_before = ctx.core.nf_alive("amf")
        if alive_before is None:
            return TestResult(self.id, self.name, "na",
                              notes="cannot observe AMF liveness (adapter can't detect a crash)")
        sent, detail = _sctp_garbage(amf, port)
        time.sleep(1.5)
        alive_after = ctx.core.nf_alive("amf")
        ok = alive_after is True
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"sctp_sent": sent, "amf_alive_after": alive_after},
                          notes=f"{detail}; AMF alive after malformed N2 input = {alive_after} "
                                f"({'no crash' if ok else 'CRASHED — remote DoS'}) [TS 38.413 §8.7.5]")


class AmfSec04(NfTestCase):
    id, name, nf = "AMF-SEC-04", "Invalid credentials denied registration (no auth bypass)", "amf"
    def run(self, ctx: RunContext):
        if ctx.driver is None or not hasattr(ctx.driver, "observe_bad_auth"):
            return TestResult(self.id, self.name, "na", notes="no UERANSIM driver for negative attach")
        o = ctx.driver.observe_bad_auth()
        if o.get("error"):
            return TestResult(self.id, self.name, "na", notes=f"negative attach unavailable: {o['error']}")
        denied = o.get("denied") is True and o.get("auth_failure") is True
        return TestResult(self.id, self.name, "pass" if denied else "fail",
                          metrics={"denied": o.get("denied"), "auth_failure": o.get("auth_failure"),
                                   "registered": o.get("registered")},
                          notes=("wrong-key UE -> AUTN MAC failure -> Authentication Reject -> "
                                 "registration denied (no auth bypass) [TS 33.501 §6.1.3]"
                                 if denied else
                                 f"invalid-credential UE was NOT properly denied "
                                 f"(registered={o.get('registered')})"))


class AmfSec06(NfTestCase):
    id, name, nf = "AMF-SEC-06", "SUPI confidentiality (SUCI concealment on N2)", "amf"
    def run(self, ctx):
        o = _obs(ctx)
        if "supi_concealed" not in o:
            return TestResult(self.id, self.name, "na",
                              notes="SUCI not captured/decoded (needs N2 capture) — cannot judge")
        scheme = o.get("suci_scheme", "?")
        if o["supi_concealed"]:
            return TestResult(self.id, self.name, "pass", metrics={"suci_scheme": scheme},
                              notes=f"SUPI concealed on N2 via SUCI ({scheme}) [TS 33.512 / 33.501 §6.12]")
        return TestResult(self.id, self.name, "fail", metrics={"suci_scheme": scheme},
                          notes=f"SUPI exposed on N2 — SUCI uses {scheme} (permanent identity "
                                f"derivable in cleartext) [TS 33.512 / 33.501 §6.12]")


class AmfSec07(NfTestCase):
    id, name, nf = "AMF-SEC-07", "SBI (Namf) requires TLS + valid OAuth2 token", "amf"
    def run(self, ctx):
        # Protocol-facing SCAS: an unauthenticated Namf request must be rejected.
        return sbi_common.reject_unauth(ctx, self.id, self.name,
                                        "/namf-comm/v1/subscriptions", method="POST", body={})


TESTS = [AmfNgap01, AmfNgap02, AmfNgap03, AmfNgap04, AmfReg01, AmfAuth01, AmfAuth02,
         AmfDereg01, AmfNeg01, AmfSec01, AmfSec02, AmfSec04, AmfSec06, AmfSec07]
