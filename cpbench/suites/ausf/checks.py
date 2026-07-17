"""Real AUSF test cases.

  AUSF-SEC-02  Nausf rejects unauthenticated requests          [TS 33.501 §13]
  AUSF-NEG-01  malformed auth request rejected, AUSF alive      [TS 29.509]
  AUSF-AUTH-01 UE authentication runs (5G-AKA)                  [TS 29.509 / 33.501]

AUSF-AUTH-01 is verified *transitively*: a successful UE registration proves the AUSF ran the
5G-AKA UEAuthentication exchange (a direct Nausf probe is blocked by the NRF-enforced OAuth2,
so it is graded from the real attach, with that stated in the note — never a silent pass).
The wrong-RES negative (AUSF-AUTH-03) needs a bad-credential attach not built yet -> stub/na.
"""
from __future__ import annotations

from cntc_common.results import TestResult
from cpbench.suites.base import NfTestCase, RunContext
from cpbench.suites import sbi_common


class AusfSec02(NfTestCase):
    id, name, nf = "AUSF-SEC-02", "SBI (Nausf) requires TLS + valid OAuth2 token", "ausf"
    def run(self, ctx):
        return sbi_common.reject_unauth(ctx, self.id, self.name,
                                        "/nausf-auth/v1/ue-authentications", method="POST", body={})


class AusfNeg01(NfTestCase):
    id, name, nf = "AUSF-NEG-01", "Malformed auth request -> reject, no crash", "ausf"
    def run(self, ctx):
        return sbi_common.malformed_rejected(ctx, self.id, self.name,
                                             "/nausf-auth/v1/ue-authentications", method="POST")


class AusfAuth01(NfTestCase):
    id, name, nf = "AUSF-AUTH-01", "UE authentication (5G-AKA) runs", "ausf"
    def run(self, ctx: RunContext):
        if ctx.driver is None or not hasattr(ctx.driver, "observe_registration"):
            return TestResult(self.id, self.name, "na", notes="no UERANSIM driver to drive auth")
        o = ctx.driver.observe_registration()
        if o.get("registered") is True:
            return TestResult(self.id, self.name, "pass",
                              metrics={"registered": True},
                              notes="verified transitively: UE completed 5G-AKA + registration, "
                                    "so AUSF ran UEAuthentication (direct Nausf blocked by OAuth2) "
                                    "[TS 33.501 §6.1.3.2]")
        if o.get("error"):
            return TestResult(self.id, self.name, "na",
                              notes=f"UERANSIM attach unavailable: {o.get('error')}")
        return TestResult(self.id, self.name, "fail",
                          metrics={"auth_request": o.get("auth_request"),
                                   "registered": o.get("registered")},
                          notes="registration did not complete — 5G-AKA not confirmed")


class AusfAuth02(NfTestCase):
    id, name, nf = "AUSF-AUTH-02", "Confirm RES* — successful authentication", "ausf"
    def run(self, ctx: RunContext):
        if ctx.driver is None or not hasattr(ctx.driver, "observe_registration"):
            return TestResult(self.id, self.name, "na", notes="no UERANSIM driver")
        o = ctx.driver.observe_registration()
        if o.get("registered") is True:
            return TestResult(self.id, self.name, "pass", metrics={"registered": True},
                              notes="registration completed -> AUSF confirmed RES* "
                                    "(Nausf_UEAuthentication result=SUCCESS) [TS 33.501 §6.1.3.2]")
        if o.get("error"):
            return TestResult(self.id, self.name, "na",
                              notes=f"UERANSIM attach unavailable: {o.get('error')}")
        return TestResult(self.id, self.name, "fail", notes="registration did not complete")


TESTS = [AusfSec02, AusfNeg01, AusfAuth01, AusfAuth02]
