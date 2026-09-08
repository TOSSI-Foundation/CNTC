"""Real UDR test cases, driven over Nudr_DataRepository.

**Every assertion below is taken from the published OpenAPI definitions, not from what this
deployment happens to return.** The resource paths come from TS 29.505; the response schemas
and their mandatory fields come from TS 29.503 (Nudm_SDM), which TS 29.505 references, and from
TS 29.519 for policy data. Where a schema declares no mandatory field, this states so rather
than inventing one, because a test that demands a field the specification makes optional would
fail a conforming implementation.

  UDR-DR-01   am-data                        AccessAndMobilitySubscriptionData   no mandatory field
  UDR-DR-02   sm-data                        SmSubsData -> SessionManagementSubscriptionData
                                             mandatory: singleNssai
  UDR-DR-03   smf-selection-subscription-data SmfSelectionSubscriptionData       no mandatory field
  UDR-DR-04   authentication-subscription    AuthenticationSubscription          mandatory: authenticationMethod
  UDR-DR-05   policy-data am-data            AmPolicyData                        no mandatory field
  UDR-SEC-01  Nudr authorization                                                 TS 33.501
  UDR-NEG-01  unknown SUPI                                                       TS 29.505

**Extra fields are not a violation.** None of these schemas sets `additionalProperties: false`,
so OpenAPI permits members the schema does not name. This deployment returns `tenantId`, `ueId`
and `servingPlmnId` on am-data; those are allowed, and reporting them as defects would be wrong.

**Why direct probes rather than transitive.** The UDM suite grades some requirements
transitively because OAuth2 blocks a direct Nudm probe. The UDR is the system of record and its
data types are individually addressable, so each is fetched and its response inspected.
"""
from __future__ import annotations

from typing import Any

from cntc_common.results import TestResult
from cpbench.suites.base import NfTestCase, RunContext
from cpbench.suites import sbi_common

_SUPI = "imsi-208930000000001"
_PLMN = "20893"
_SUB = f"/nudr-dr/v2/subscription-data/{_SUPI}"


def _get(ctx: RunContext, path: str) -> dict[str, Any]:
    """One Nudr GET, with a token when the NRF will grant one, else without.

    Whether the UDR *should* demand a token is UDR-SEC-01's question, not this one. Here we are
    only asking whether the data type is served, so a deployment that does not enforce
    authorization must not make every protocol case unjudgeable.
    """
    base = sbi_common.sbi_base(ctx)
    tok = ctx.sbi.get_access_token(base, nf_type="UDM", target_nf_type="UDR", scope="nudr-dr")
    r = ctx.sbi.request("GET", f"{base}{path}", token=tok.get("token"))
    r["with_token"] = bool(tok.get("token"))
    return r


def _served(ctx: RunContext, tid: str, name: str, path: str, what: str,
            schema: str, mandatory: tuple[str, ...], known: tuple[str, ...],
            spec: str, inside: str = "") -> TestResult:
    """PASS iff the UDR serves the resource and the body satisfies the schema's mandatory set.

    ``mandatory`` is exactly the schema's ``required`` list, which for several of these types is
    empty. When it is empty the assertion is that the resource is served and the body is a JSON
    object of the declared shape, and the note says so, so nobody reads a pass as more than it is.

    ``known`` is the schema's declared properties, reported as evidence only. Members outside it
    are permitted, because none of these schemas forbids additional properties.
    """
    if ctx.sbi is None:
        return TestResult(tid, name, "na", notes="no SBI client wired")
    r = _get(ctx, path)
    st, body, tok = r.get("status"), r.get("body"), r.get("with_token")

    if st in (401, 403):
        return TestResult(tid, name, "na", metrics={"status": st},
                          notes=f"the UDR refused the request ({st}), so {what} could not be "
                                f"retrieved to judge [{spec}]")
    if st == 404:
        return TestResult(tid, name, "fail", metrics={"status": 404},
                          notes=f"the UDR has no {what} for {_SUPI}. The subscriber may not be "
                                f"provisioned, or the data type is not stored [{spec}]")
    if st != 200:
        return TestResult(tid, name, "fail", metrics={"status": st},
                          notes=f"{what} retrieval returned HTTP {st}, the specification defines "
                                f"200 for this resource [{spec}]")

    if isinstance(body, list):
        body = body[0] if body else {}
    # sm-data is returned as SmSubsData, which carries the subscription records in
    # individualSmSubsData. Descending is what the schema says to do, not a local workaround.
    if inside and isinstance(body, dict) and inside in body:
        inner = body[inside]
        if isinstance(inner, list):
            inner = inner[0] if inner else {}
        if isinstance(inner, dict):
            body = inner
    if not isinstance(body, dict):
        return TestResult(tid, name, "fail", metrics={"status": 200, "type": type(body).__name__},
                          notes=f"{what} returned 200 but the body is not a JSON object, and "
                                f"{schema} is an object [{spec}]")

    missing = [k for k in mandatory if k not in body]
    if missing:
        return TestResult(tid, name, "fail",
                          metrics={"status": 200, "missing": missing, "present": sorted(body)[:10]},
                          notes=f"{what} is missing {', '.join(missing)}, which {schema} declares "
                                f"mandatory [{spec}]")

    seen = [k for k in known if k in body]
    extra = [k for k in body if k not in known]
    if mandatory:
        why = f"mandatory field(s) {', '.join(mandatory)} present"
    else:
        why = (f"{schema} declares no mandatory field, so this asserts only that the resource is "
               f"served as an object of that schema")
    return TestResult(tid, name, "pass",
                      metrics={"status": 200, "schema": schema, "mandatory_present": list(mandatory),
                               "schema_fields_seen": seen, "extra_fields": extra,
                               "with_token": tok},
                      notes=f"{what} served for {_SUPI}: {why}"
                            f"{'; carrying ' + ', '.join(seen) if seen else ''}"
                            f"{'; plus permitted extra member(s) ' + ', '.join(extra) if extra else ''} "
                            f"({'with' if tok else 'without'} an OAuth2 token) [{spec}]")


class UdrDr01(NfTestCase):
    id, name, nf = "UDR-DR-01", "Access and Mobility subscription data retrieval (am-data)", "udr"
    def run(self, ctx):
        return _served(ctx, self.id, self.name,
                       f"{_SUB}/{_PLMN}/provisioned-data/am-data",
                       "access and mobility subscription data",
                       "AccessAndMobilitySubscriptionData",
                       mandatory=(),  # TS 29.503 declares no required member for this schema
                       known=("gpsis", "nssai", "subscribedUeAmbr", "rfspIndex", "subsRegTimer",
                              "ueUsageType", "mpsPriority", "activeTime", "ratRestrictions",
                              "forbiddenAreas", "serviceAreaRestriction", "coreNetworkTypeRestrictions"),
                       spec="TS 29.505 am-data, schema AccessAndMobilitySubscriptionData (TS 29.503)")


class UdrDr02(NfTestCase):
    id, name, nf = "UDR-DR-02", "Session Management subscription data retrieval (sm-data)", "udr"
    def run(self, ctx):
        return _served(ctx, self.id, self.name,
                       f"{_SUB}/{_PLMN}/provisioned-data/sm-data",
                       "session management subscription data",
                       "SessionManagementSubscriptionData",
                       mandatory=("singleNssai",),   # the one required member in TS 29.503
                       known=("singleNssai", "dnnConfigurations", "internalGroupIds",
                              "sharedDnnConfigurationsId", "odbPacketServices", "traceData",
                              "supportedFeatures"),
                       spec="TS 29.505 sm-data, schema SmSubsData -> SessionManagementSubscriptionData (TS 29.503)",
                       inside="individualSmSubsData")


class UdrDr03(NfTestCase):
    id, name, nf = "UDR-DR-03", "SMF selection subscription data retrieval", "udr"
    def run(self, ctx):
        return _served(ctx, self.id, self.name,
                       f"{_SUB}/{_PLMN}/provisioned-data/smf-selection-subscription-data",
                       "SMF selection subscription data",
                       "SmfSelectionSubscriptionData",
                       mandatory=(),  # no required member in TS 29.503
                       known=("subscribedSnssaiInfos", "sharedSnssaiInfosId", "hssGroupId",
                              "supportedFeatures"),
                       spec="TS 29.505 smf-selection-subscription-data, schema "
                            "SmfSelectionSubscriptionData (TS 29.503)")


class UdrDr04(NfTestCase):
    id, name, nf = "UDR-DR-04", "Authentication subscription data retrieval (5G-AKA credentials)", "udr"
    def run(self, ctx):
        # This is what lets the UDM build an authentication vector, so 5G-AKA depends on it.
        # Only which fields were present is recorded; no key material is ever read out.
        return _served(ctx, self.id, self.name,
                       f"{_SUB}/authentication-data/authentication-subscription",
                       "authentication subscription data",
                       "AuthenticationSubscription",
                       mandatory=("authenticationMethod",),  # the one required member
                       known=("authenticationMethod", "encPermanentKey", "encOpcKey",
                              "encTopcKey", "protectionParameterId", "authenticationManagementField",
                              "algorithmId", "sequenceNumber", "vectorGenerationInHss",
                              "n5gcAuthMethod", "rgAuthenticationInd", "routingId", "akmaAllowed"),
                       spec="TS 29.505 authentication-subscription, schema AuthenticationSubscription")


class UdrDr05(NfTestCase):
    id, name, nf = "UDR-DR-05", "Policy data retrieval for a UE (am-policy-data)", "udr"
    def run(self, ctx):
        return _served(ctx, self.id, self.name,
                       f"/nudr-dr/v2/policy-data/ues/{_SUPI}/am-data",
                       "access and mobility policy data",
                       "AmPolicyData",
                       mandatory=(),  # TS 29.519 declares no required member
                       known=("praInfos", "subscCats"),
                       spec="TS 29.519 policy-data/ues/{ueId}/am-data, schema AmPolicyData")


class UdrSec01(NfTestCase):
    id, name, nf = "UDR-SEC-01", "SBI (Nudr) requires TLS + valid OAuth2 token", "udr"
    def run(self, ctx):
        return sbi_common.reject_unauth(
            ctx, self.id, self.name,
            f"{_SUB}/{_PLMN}/provisioned-data/am-data", method="GET")


class UdrNeg01(NfTestCase):
    id, name, nf = "UDR-NEG-01", "Unknown SUPI -> 404 ProblemDetails, no crash", "udr"

    def run(self, ctx: RunContext) -> TestResult:
        if ctx.sbi is None:
            return TestResult(self.id, self.name, "na", notes="no SBI client wired")
        unknown = "imsi-208930000009999"
        r = _get(ctx, f"/nudr-dr/v2/subscription-data/{unknown}/{_PLMN}/provisioned-data/am-data")
        st = r.get("status")

        # Liveness after the probe matters as much as the status code: a repository that
        # answers correctly and then dies has still failed.
        alive = ctx.core.nf_alive("udr") if ctx.core is not None else None
        if alive is False:
            return TestResult(self.id, self.name, "fail",
                              metrics={"status": st, "alive_after": False},
                              notes="the UDR stopped running after a request for an unknown "
                                    "SUPI [TS 29.505 subscription-data resources]")
        if st == 404:
            return TestResult(self.id, self.name, "pass",
                              metrics={"status": 404, "alive_after": alive},
                              notes="unknown SUPI rejected with 404 and the UDR stayed up "
                                    "[TS 29.505 subscription-data resources]")
        if st in (401, 403):
            return TestResult(self.id, self.name, "na",
                              metrics={"status": st},
                              notes=f"the UDR refused the request ({st}) before it could reach "
                                    f"the lookup, so its handling of an unknown SUPI was not "
                                    f"exercised")
        if st == 200:
            return TestResult(self.id, self.name, "fail",
                              metrics={"status": 200},
                              notes=f"the UDR returned data for {unknown}, which is not a "
                                    f"provisioned subscriber [TS 29.505 subscription-data resources]")
        return TestResult(self.id, self.name, "fail", metrics={"status": st},
                          notes=f"unknown SUPI returned HTTP {st}, expected 404 [TS 29.505 subscription-data resources]")


TESTS = [UdrDr01, UdrDr02, UdrDr03, UdrDr04, UdrDr05, UdrSec01, UdrNeg01]
