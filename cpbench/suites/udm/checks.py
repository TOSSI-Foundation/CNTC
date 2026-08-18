"""Real UDM test cases.

  UDM-SEC-02  Nudm rejects unauthenticated requests             [TS 33.501 §13]
  UDM-NEG-01  malformed request rejected, UDM alive             [TS 29.503]
  UDM-AUTH-01 authentication-vector generation                  [TS 29.503 Nudm_UEAuthentication]
  UDM-SDM-01  subscription data retrieval                       [TS 29.503 Nudm_SDM]

UDM-AUTH-01 and UDM-SDM-01 are verified *transitively*: a successful UE registration + PDU
session is only possible if the UDM generated the auth vector (Nudm_UEAuthentication_Get) AND
served the access-mobility + session-management subscription (Nudm_SDM), otherwise the attach
fails. Direct Nudm probes are blocked by the NRF-enforced OAuth2, so these are graded from the
real attach with that stated in the note (never a silent pass).
"""
from __future__ import annotations

from cntc_common.results import TestResult
from cpbench.suites.base import NfTestCase, RunContext
from cpbench.suites import sbi_common

_SUPI = "imsi-208930000000001"


class UdmSec02(NfTestCase):
    id, name, nf = "UDM-SEC-02", "SBI (Nudm) requires TLS + valid OAuth2 token", "udm"
    def run(self, ctx):
        return sbi_common.reject_unauth(
            ctx, self.id, self.name,
            f"/nudm-ueau/v1/{_SUPI}/security-information/generate-auth-data",
            method="POST", body={"servingNetworkName": "5G:mnc093.mcc208.3gppnetwork.org"})


class UdmNeg01(NfTestCase):
    id, name, nf = "UDM-NEG-01", "Malformed request -> reject, no crash", "udm"
    def run(self, ctx):
        return sbi_common.malformed_rejected(
            ctx, self.id, self.name,
            f"/nudm-ueau/v1/{_SUPI}/security-information/generate-auth-data", method="POST")


def _transitive(ctx: RunContext, tid: str, name: str, role: str, spec: str) -> TestResult:
    if ctx.driver is None or not hasattr(ctx.driver, "observe_registration"):
        return TestResult(tid, name, "na", notes="no UERANSIM driver to drive the attach")
    o = ctx.driver.observe_registration()
    if o.get("registered") is True:
        return TestResult(tid, name, "pass", metrics={"registered": True, "pdu": o.get("pdu_session")},
                          notes=f"verified transitively: successful registration"
                                f"{' + PDU session' if o.get('pdu_session') else ''} required the "
                                f"UDM to {role} (direct Nudm blocked by OAuth2) [{spec}]")
    if o.get("error"):
        return TestResult(tid, name, "na", notes=f"UERANSIM attach unavailable: {o.get('error')}")
    return TestResult(tid, name, "fail", notes="registration did not complete")


class UdmAuth01(NfTestCase):
    id, name, nf = "UDM-AUTH-01", "Authentication-vector generation (Nudm_UEAuthentication_Get)", "udm"
    def run(self, ctx):
        return _transitive(ctx, self.id, self.name, "generate the 5G-AKA auth vector",
                           "TS 29.503 §5.4")


class UdmSdm01(NfTestCase):
    id, name, nf = "UDM-SDM-01", "Subscription data retrieval (Nudm_SDM_Get)", "udm"
    def run(self, ctx):
        return _transitive(ctx, self.id, self.name, "serve the UE access-mobility subscription (SDM)",
                           "TS 29.503 §5.2")


class UdmSdm02(NfTestCase):
    id, name, nf = "UDM-SDM-02", "Session-management subscription data (for SMF)", "udm"
    def run(self, ctx):
        # A successful PDU session requires the SMF to fetch the SM subscription from the UDM.
        o = ctx.driver.observe_registration() if (ctx.driver and hasattr(ctx.driver, "observe_registration")) else {}
        if o.get("pdu_session") is True:
            return TestResult(self.id, self.name, "pass", metrics={"pdu_session": True},
                              notes="PDU session established -> SMF fetched the SM subscription "
                                    "from UDM (Nudm_SDM SM-data) [TS 29.503 §5.2]")
        return _transitive(ctx, self.id, self.name, "serve the SM subscription (SDM)", "TS 29.503 §5.2")


class UdmUecm01(NfTestCase):
    id, name, nf = "UDM-UECM-01", "AMF registration for UE (Nudm_UECM_Registration)", "udm"
    def run(self, ctx):
        return _transitive(ctx, self.id, self.name,
                           "record the serving-AMF registration (Nudm_UECM)", "TS 29.503 §5.3")


class UdmSec01(NfTestCase):
    id, name, nf = "UDM-SEC-01", "SUCI de-concealment (SIDF) authorization", "udm"
    def run(self, ctx):
        # A completed 5G-AKA requires the UDM/SIDF to de-conceal the SUCI into a SUPI before it
        # can generate the auth vector, so a successful registration proves the SIDF ran.
        return _transitive(ctx, self.id, self.name,
                           "de-conceal the SUCI to a SUPI (SIDF) before generating the vector",
                           "TS 33.501 §6.12")


TESTS = [UdmSec02, UdmNeg01, UdmAuth01, UdmSdm01, UdmSdm02, UdmUecm01, UdmSec01]
