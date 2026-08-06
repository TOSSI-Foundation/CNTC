"""Reusable SBI security/robustness checks shared by the NRF/AUSF/UDM suites.

These encode the *protocol-facing* SCAS requirements that apply to every SBI-exposing NF:
  - authorization: an unauthenticated service request must be rejected (TS 33.501 §13)
  - transport security: the SBI endpoint must be TLS-protected (TS 33.310 / 33.5xx)
  - robustness: a malformed request must be rejected (4xx) with the NF still alive (TS 29.5xx)
Keeping them in one place means every NF is judged by the exact same rule.
"""
from __future__ import annotations

from cntc_common.results import TestResult
from cpbench.suites.base import RunContext


def _host_port(ctx: RunContext) -> tuple[str, int]:
    ep = ctx.endpoint or ctx.core.nf_endpoint(ctx.nf)
    host, _, port = ep.partition(":")
    return host, int(port or 8000)


# Cache the detected scheme per host:port so we probe TLS once per campaign, not per test.
_SCHEME_CACHE: dict[tuple[str, int], str] = {}


def sbi_scheme(ctx: RunContext, host: str, port: int) -> str:
    """'https' if the SBI port speaks TLS, else 'http'. Detected once and cached.

    free5GC on docker serves SBI as cleartext HTTP; the SD-Core/Aether k8s chart serves it
    over HTTPS. Hardcoding a scheme makes every request to the *other* kind of deployment fail
    (cleartext to a TLS port -> HTTP 400 / reset), which used to be misread as a security
    finding. Probing the port makes the SBI checks work — and judge honestly — on both.
    """
    key = (host, port)
    if key not in _SCHEME_CACHE:
        scheme = "http"
        try:
            if ctx.sbi is not None and ctx.sbi.tls_probe(host, port).get("tls"):
                scheme = "https"
        except Exception:  # noqa: BLE001
            scheme = "http"
        _SCHEME_CACHE[key] = scheme
    return _SCHEME_CACHE[key]


def sbi_base(ctx: RunContext) -> str:
    """Base URL for the NF's SBI with the scheme auto-detected (https on TLS, else http)."""
    host, port = _host_port(ctx)
    return f"{sbi_scheme(ctx, host, port)}://{host}:{port}"


def reject_unauth(ctx: RunContext, tid: str, name: str, path: str,
                  method: str = "GET", body=None) -> TestResult:
    """PASS iff a token-less call to an SBI service is rejected with 401/403."""
    if ctx.sbi is None:
        return TestResult(tid, name, "na", notes="no SBI client wired")
    r = ctx.sbi.request(method, f"{sbi_base(ctx)}{path}", json=body)
    st = r.get("status")
    if st is None:
        return TestResult(tid, name, "na", notes=f"{ctx.nf.upper()} unreachable: {r.get('error')}")
    # Honest grading of a token-less SBI call:
    #   401/403        -> authorization enforced (PASS)
    #   2xx            -> protected data served WITHOUT a token = a real auth bypass (FAIL)
    #   anything else  -> 400/404/5xx: the request was NOT served, but not on auth grounds
    #                     (often wrong endpoint shape) — we cannot conclude either way (na).
    if st in (401, 403):
        return TestResult(tid, name, "pass", metrics={"status": st},
                          notes=f"unauthenticated {method} {path} -> HTTP {st} (rejected)")
    if 200 <= st < 300:
        return TestResult(tid, name, "fail", metrics={"status": st},
                          notes=f"unauthenticated {method} {path} -> HTTP {st} "
                                f"(served without a token — authz bypass)")
    return TestResult(tid, name, "na", metrics={"status": st},
                      notes=f"unauthenticated {method} {path} -> HTTP {st} — not an auth "
                            f"decision (request not served, but not 401/403); cannot judge authz")


def requires_tls(ctx: RunContext, tid: str, name: str) -> TestResult:
    """PASS iff the SBI endpoint answers over TLS; FAIL if it is cleartext-only."""
    if ctx.sbi is None:
        return TestResult(tid, name, "na", notes="no SBI client wired")
    host, port = _host_port(ctx)
    tls = ctx.sbi.tls_probe(host, port)
    if tls.get("tls"):
        return TestResult(tid, name, "pass", metrics={"tls": True},
                          notes=f"TLS handshake ok (HTTPS {tls.get('status')})")
    return TestResult(tid, name, "fail", metrics={"tls": False},
                      notes=f"no TLS listener on {host}:{port} ({tls.get('error')}) — "
                            f"SBI served cleartext (not TLS-protected)")


def malformed_rejected(ctx: RunContext, tid: str, name: str, path: str,
                       method: str = "POST", body="NOTJSON{{{") -> TestResult:
    """PASS iff a malformed request is rejected (4xx) and the NF stays alive (no crash)."""
    if ctx.sbi is None:
        return TestResult(tid, name, "na", notes="no SBI client wired")
    host, port = _host_port(ctx)
    r = ctx.sbi.request(method, f"http://{host}:{port}{path}", data=body)
    st = r.get("status")
    alive = ctx.core.nf_alive(ctx.nf)
    if st is None:
        return TestResult(tid, name, "na", notes=f"{ctx.nf.upper()} unreachable: {r.get('error')}")
    if alive is False:
        return TestResult(tid, name, "fail",
                          notes=f"{ctx.nf.upper()} not alive after malformed request (HTTP {st})")
    rejected = 400 <= st < 500
    return TestResult(tid, name, "pass" if rejected else "fail",
                      metrics={"status": st, "alive": alive},
                      notes=f"malformed {method} {path} -> HTTP {st}; {ctx.nf.upper()} alive={alive}")
