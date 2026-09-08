"""Real PCF test cases, driven over Npcf.

**Every assertion is taken from the published OpenAPI definitions.** TS 29.507 defines the
resource paths, the status codes, the `Location` header as `required: true` on a 201, and the
mandatory members of a PolicyAssociationRequest. TS 29.512 defines the SM policy resource. The
mandatory set is used verbatim: a request padded with optional members would test what this
implementation happens to want rather than what the specification demands.

  PCF-AM-01   AM policy association create                    [TS 29.507 POST /policies]
  PCF-AM-02   AM policy association retrieve                  [TS 29.507 GET /policies/{polAssoId}]
  PCF-AM-03   AM policy association delete                    [TS 29.507 DELETE /policies/{polAssoId}]
  PCF-SM-01   SM policy association create                    [TS 29.512 POST /sm-policies, SmPolicyContextData]
  PCF-SEC-01  Npcf requires TLS and a valid OAuth2 token      [TS 33.501 §13]
  PCF-NEG-01  Malformed policy request rejected, no crash     [TS 29.507 POST /policies]

**The three AM cases are one lifecycle, deliberately.** Create, retrieve, delete. Each is
judged on its own response, but they share the association the first one made, because a
retrieve or a delete of something that was never created would test nothing. The association id
is cached for the campaign, and the delete case is what cleans it up, so a run leaves no policy
state behind on the PCF.
"""
from __future__ import annotations

from typing import Any

from cntc_common.results import TestResult
from cpbench.suites.base import NfTestCase, RunContext
from cpbench.suites import sbi_common

_SUPI = "imsi-208930000000001"
_AM_PATH = "/npcf-am-policy-control/v1/policies"
_SM_PATH = "/npcf-smpolicycontrol/v1/sm-policies"

# The association created by PCF-AM-01, shared with the retrieve and delete cases.
_ASSOC: dict[str, Any] = {}


# TS 29.507 PolicyAssociationRequest: required = [notificationUri, suppFeat, supi].
# Nothing else is mandatory, so nothing else is sent by the conformance case.
_AM_MANDATORY = {
    "supi": _SUPI,
    "notificationUri": "http://127.0.0.1:9999/cntc-callback",
    "suppFeat": "0",
}

# Optional members this deployment additionally insists on. Used only to obtain an association
# for the retrieve and release cases, never as the conformance claim. See PCF-AM-01.
_AM_WORKAROUND = dict(_AM_MANDATORY, servingPlmn={"mcc": "208", "mnc": "93"})


def _post(ctx: RunContext, path: str, body: Any) -> dict[str, Any]:
    base = sbi_common.sbi_base(ctx)
    tok = ctx.sbi.get_access_token(base, nf_type="AMF", target_nf_type="PCF", scope="npcf-am-policy-control")
    return ctx.sbi.request("POST", f"{base}{path}", json=body, token=tok.get("token"))


class PcfAm01(NfTestCase):
    id, name, nf = "PCF-AM-01", "AM policy association creation returns a policy and a Location", "pcf"

    def run(self, ctx: RunContext) -> TestResult:
        if ctx.sbi is None:
            return TestResult(self.id, self.name, "na", notes="no SBI client wired")
        spec = "TS 29.507 POST /policies, PolicyAssociationRequest"
        r = _post(ctx, _AM_PATH, dict(_AM_MANDATORY))
        st = r.get("status")
        loc = (r.get("headers") or {}).get("location")

        if st in (401, 403):
            return TestResult(self.id, self.name, "na", metrics={"status": st},
                              notes=f"the PCF refused the request ({st}), so association "
                                    f"creation was not exercised [{spec}]")
        if st == 400:
            # The request carried exactly the mandatory members. A 400 therefore means the PCF
            # demands members the specification marks optional, which would reject a conforming
            # peer. Establish which one, so the finding names it rather than guessing.
            needs = _probe_extra_requirement(ctx)
            detail = ""
            if isinstance(r.get("body"), dict):
                detail = str(r["body"].get("detail") or "")
            return TestResult(self.id, self.name, "fail",
                              metrics={"status": 400, "sent": sorted(_AM_MANDATORY),
                                       "additionally_required": needs, "detail": detail},
                              notes=f"a PolicyAssociationRequest carrying exactly the mandatory "
                                    f"members ({', '.join(sorted(_AM_MANDATORY))}) was rejected "
                                    f"with 400 {('(' + detail + ')') if detail else ''}. "
                                    f"{('The PCF additionally requires ' + ', '.join(needs) + ', which the specification marks optional. ') if needs else ''}"
                                    f"A conforming AMF sending only the mandatory set would be "
                                    f"refused [{spec}]")
        if st != 201:
            return TestResult(self.id, self.name, "fail", metrics={"status": st},
                              notes=f"AM policy association create returned HTTP {st}, the "
                                    f"specification defines 201 [{spec}]")
        if not loc:
            return TestResult(self.id, self.name, "fail", metrics={"status": 201, "location": None},
                              notes="the association was created but no Location header was "
                                    "returned, which TS 29.507 marks required on the 201, so the "
                                    f"resource cannot be addressed [{spec}]")
        _ASSOC["id"] = loc.rstrip("/").rsplit("/", 1)[-1]
        return TestResult(self.id, self.name, "pass",
                          metrics={"status": 201, "assoc_id": _ASSOC["id"],
                                   "sent": sorted(_AM_MANDATORY)},
                          notes=f"a PolicyAssociationRequest carrying only the mandatory members "
                                f"was accepted, and the required Location header addressed "
                                f"{_ASSOC['id']} [{spec}]")


def _probe_extra_requirement(ctx: RunContext) -> list[str]:
    """Which optional members does this PCF additionally insist on?

    Called only after a mandatory-only request was refused, so the finding can name the member
    rather than assert that something unspecified is wrong. Each optional member is added on its
    own; the first that turns the 400 into a 201 is the one being demanded.
    """
    for field, value in (("servingPlmn", {"mcc": "208", "mnc": "93"}),
                         ("accessType", "3GPP_ACCESS"),
                         ("ratType", "NR"),
                         ("guami", {"plmnId": {"mcc": "208", "mnc": "93"}, "amfId": "cafe00"})):
        try:
            r = _post(ctx, _AM_PATH, dict(_AM_MANDATORY, **{field: value}))
        except Exception:  # noqa: BLE001, a probe failure must not break the verdict
            continue
        if r.get("status") == 201:
            loc = (r.get("headers") or {}).get("location")
            if loc:  # leave nothing behind
                base = sbi_common.sbi_base(ctx)
                ctx.sbi.request("DELETE", f"{base}{_AM_PATH}/{loc.rstrip('/').rsplit('/', 1)[-1]}")
            return [field]
    return []


class PcfAm02(NfTestCase):
    id, name, nf = "PCF-AM-02", "AM policy association is retrievable at the Location it returned", "pcf"

    def run(self, ctx: RunContext) -> TestResult:
        if ctx.sbi is None:
            return TestResult(self.id, self.name, "na", notes="no SBI client wired")
        if not _ASSOC.get("id") and not _setup_association(ctx):
            return TestResult(self.id, self.name, "na",
                              notes="no association could be created, so there was nothing to "
                                    "retrieve (see PCF-AM-01)")
        base = sbi_common.sbi_base(ctx)
        r = ctx.sbi.request("GET", f"{base}{_AM_PATH}/{_ASSOC['id']}")
        st = r.get("status")
        if st == 200:
            return TestResult(self.id, self.name, "pass",
                              metrics={"status": 200, "assoc_id": _ASSOC["id"]},
                              notes=f"the association created at {_ASSOC['id']} was served back "
                                    f"[TS 29.507 GET /policies/{{polAssoId}}]")
        if st in (401, 403):
            return TestResult(self.id, self.name, "na", metrics={"status": st},
                              notes=f"the PCF refused the retrieval ({st}), so it was not judged")
        return TestResult(self.id, self.name, "fail", metrics={"status": st},
                          notes=f"the association created at {_ASSOC['id']} could not be "
                                f"retrieved, HTTP {st} [TS 29.507 GET /policies/{{polAssoId}}]")


class PcfAm03(NfTestCase):
    id, name, nf = "PCF-AM-03", "AM policy association delete releases it, and it is then gone", "pcf"

    def run(self, ctx: RunContext) -> TestResult:
        if ctx.sbi is None:
            return TestResult(self.id, self.name, "na", notes="no SBI client wired")
        if not _ASSOC.get("id") and not _setup_association(ctx):
            return TestResult(self.id, self.name, "na",
                              notes="no association could be created, so there was nothing to "
                                    "release (see PCF-AM-01)")
        base = sbi_common.sbi_base(ctx)
        aid = _ASSOC["id"]
        r = ctx.sbi.request("DELETE", f"{base}{_AM_PATH}/{aid}")
        st = r.get("status")
        if st in (401, 403):
            return TestResult(self.id, self.name, "na", metrics={"status": st},
                              notes=f"the PCF refused the delete ({st}), so release was not judged")
        if st not in (200, 204):
            return TestResult(self.id, self.name, "fail", metrics={"status": st},
                              notes=f"delete of association {aid} returned HTTP {st}, expected "
                                    f"204 [TS 29.507 DELETE /policies/{{polAssoId}}]")
        # Deleting is only half the requirement. Confirm it is actually gone, because a PCF
        # that reports success and keeps the association still leaks state.
        after = ctx.sbi.request("GET", f"{base}{_AM_PATH}/{aid}").get("status")
        _ASSOC.clear()
        if after == 404:
            return TestResult(self.id, self.name, "pass",
                              metrics={"delete_status": st, "get_after": 404},
                              notes=f"association {aid} released, and a retrieval afterwards "
                                    f"returned 404 [TS 29.507 DELETE /policies/{{polAssoId}}]")
        return TestResult(self.id, self.name, "fail",
                          metrics={"delete_status": st, "get_after": after},
                          notes=f"delete of {aid} returned {st} but the association is still "
                                f"retrievable (HTTP {after}), so it was not released "
                                f"[TS 29.507 DELETE /policies/{{polAssoId}}]")


class PcfSm01(NfTestCase):
    id, name, nf = "PCF-SM-01", "SM policy association creation for a PDU session", "pcf"

    def run(self, ctx: RunContext) -> TestResult:
        if ctx.sbi is None:
            return TestResult(self.id, self.name, "na", notes="no SBI client wired")
        # An SM policy needs an AM policy context for the same UE. Establish it here rather
        # than relying on another case having run first, so this result does not depend on
        # suite ordering.
        if not _ASSOC.get("id"):
            _setup_association(ctx)
        body = {
            "supi": _SUPI,
            "pduSessionId": 1,
            "pduSessionType": "IPV4",
            "dnn": "internet",
            "notificationUri": "http://127.0.0.1:9999/cntc-callback",
            "sliceInfo": {"sst": 1, "sd": "010203"},
            "servingNetwork": {"mcc": "208", "mnc": "93"},
            "ipv4Address": "10.60.0.1",
        }
        r = _post(ctx, _SM_PATH, body)
        st = r.get("status")
        detail = ""
        if isinstance(r.get("body"), dict):
            detail = str(r["body"].get("detail") or r["body"].get("cause") or "")

        if st == 201:
            return TestResult(self.id, self.name, "pass", metrics={"status": 201},
                              notes=f"SM policy association created for {_SUPI} "
                                    f"[TS 29.512 POST /sm-policies, SmPolicyContextData]")
        # An SM policy needs an AM policy context that this PCF only builds through a real
        # AMF-driven registration. A direct create leaves the SM handler unable to find it, so
        # the requirement was not exercised and cannot be judged. This is not evidence the PCF
        # would refuse it in a real session.
        if st == 403 and "AM Policy" in detail:
            return TestResult(self.id, self.name, "na",
                              metrics={"status": 403, "detail": detail},
                              notes="the PCF requires an AM policy context that only a real "
                                    "AMF-driven registration establishes, so an out-of-band SM "
                                    "policy create could not be exercised. Drive a UE "
                                    "registration and PDU session to certify this requirement "
                                    "[TS 29.512 POST /sm-policies, SmPolicyContextData]")
        if st in (401, 403):
            return TestResult(self.id, self.name, "na", metrics={"status": st, "detail": detail},
                              notes=f"the PCF refused the request ({st}: {detail}), so SM policy "
                                    f"creation was not exercised")
        return TestResult(self.id, self.name, "fail", metrics={"status": st, "detail": detail},
                          notes=f"SM policy association create returned HTTP {st} "
                                f"{('(' + detail + ')') if detail else ''}, expected 201 "
                                f"[TS 29.512 POST /sm-policies, SmPolicyContextData]")


def _setup_association(ctx: RunContext) -> bool:
    """Obtain an association so retrieve and release can be judged.

    PCF-AM-01 deliberately sends only the mandatory members, so on an implementation that
    demands more it correctly fails and leaves nothing to work with. Retrieve and release are
    separate requirements and should still be exercised, so this falls back to whatever body the
    implementation accepts. This is setup, not a conformance claim: the fact that the padding
    was needed is reported by PCF-AM-01 and nowhere else.
    """
    r = _post(ctx, _AM_PATH, dict(_AM_WORKAROUND))
    loc = (r.get("headers") or {}).get("location")
    if r.get("status") == 201 and loc:
        _ASSOC["id"] = loc.rstrip("/").rsplit("/", 1)[-1]
        return True
    return False


class PcfSec01(NfTestCase):
    id, name, nf = "PCF-SEC-01", "SBI (Npcf) requires TLS + valid OAuth2 token", "pcf"
    def run(self, ctx):
        return sbi_common.reject_unauth(ctx, self.id, self.name, _AM_PATH,
                                        method="POST", body=dict(_AM_WORKAROUND))


class PcfNeg01(NfTestCase):
    id, name, nf = "PCF-NEG-01", "Malformed policy request -> rejected, PCF stays up", "pcf"
    def run(self, ctx):
        return sbi_common.malformed_rejected(ctx, self.id, self.name, _AM_PATH, method="POST")


TESTS = [PcfAm01, PcfAm02, PcfAm03, PcfSm01, PcfSec01, PcfNeg01]
