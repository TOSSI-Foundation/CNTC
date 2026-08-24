"""SBI client driver: drives the Service-Based Interface directly (HTTP/2 + TLS + OAuth2).

The one significant net-new component of Stage 2. It poses as a peer NF and calls the target
NF's SBI services (Nnrf, Nausf, Nudm, Namf, Nsmf), parses ``ProblemDetails`` error bodies, and
checks TLS + OAuth2 enforcement, the driver behind the NRF/AUSF/UDM suites and the AMF/SMF
security cases. Built on ``httpx`` (HTTP/2 when the peer offers it; SBI peers that only speak
h2c fall back to HTTP/1.1, which free5GC accepts).
"""
from __future__ import annotations

from typing import Any

from cpbench.drivers.base import Driver as BaseDriver

_PROCEDURES = {
    "nf_register", "nf_update", "nf_deregister", "nf_discover", "access_token",
    "ue_authenticate", "sdm_get", "auth_vector_get", "tls_probe",
}


class Driver(BaseDriver):
    name = "sbi_client"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        self.timeout = float(cfg.core.extra.get("sbi_timeout", 5.0))
        self._client = None

    def capabilities(self) -> set[str]:
        return set(_PROCEDURES)

    def setup(self) -> None:
        import httpx
        # verify=False: SBI peers commonly use self-signed certs; we test *enforcement* of
        # TLS/OAuth2, not the PKI. http2=True negotiates h2 on TLS; http:// stays HTTP/1.1.
        self._client = httpx.Client(http2=True, timeout=self.timeout, verify=False)

    def teardown(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def request(self, method: str, url: str, token: str | None = None,
                json: Any = None, data: Any = None) -> dict[str, Any]:
        """One SBI request. Returns a structured result the tests assert on:
        {ok, status, http_version, body, problem_details, error}."""
        if self._client is None:
            self.setup()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.store.record_command(
            f"[sbi] {method} {url}" + (" (Bearer)" if token else " (no token)"))
        try:
            r = self._client.request(method, url, headers=headers, json=json, data=data)
        except Exception as e:  # noqa: BLE001, connection refused / TLS error is a result, not a crash
            return {"ok": False, "status": None, "error": f"{type(e).__name__}: {e}"}
        body: Any = None
        problem = None
        try:
            body = r.json()
            # RFC 7807 ProblemDetails per TS 29.500 carry these keys
            if isinstance(body, dict) and ("title" in body or "cause" in body or "status" in body):
                problem = body
        except Exception:  # noqa: BLE001, non-JSON body
            body = r.text[:500]
        return {"ok": 200 <= r.status_code < 300, "status": r.status_code,
                "http_version": r.http_version, "body": body, "problem_details": problem}

    def tls_probe(self, host: str, port: int) -> dict[str, Any]:
        """Is the SBI endpoint served over TLS? Attempt an HTTPS handshake; a connect/TLS
        error means the endpoint is cleartext-only (a real SCAS finding)."""
        import httpx
        url = f"https://{host}:{port}/"
        self.store.record_command(f"[sbi] TLS probe {url}")
        try:
            with httpx.Client(verify=False, timeout=self.timeout, http2=True) as c:
                r = c.get(url)
            return {"tls": True, "status": r.status_code}
        except Exception as e:  # noqa: BLE001
            return {"tls": False, "error": f"{type(e).__name__}: {e}"}

    def get_access_token(self, base: str, nf_type: str, target_nf_type: str,
                         scope: str, nf_instance_id: str = "cpbench-probe") -> dict[str, Any]:
        """OAuth2 client-credentials grant from the NRF (TS 33.501 §13). Returns
        {ok, token, status}, used to authorize discovery when the NRF enforces OAuth2."""
        r = self.request("POST", f"{base}/oauth2/token", data={
            "grant_type": "client_credentials", "nfInstanceId": nf_instance_id,
            "nfType": nf_type, "targetNfType": target_nf_type, "scope": scope})
        token = (r.get("body") or {}).get("access_token") if isinstance(r.get("body"), dict) else None
        return {"ok": bool(token), "token": token, "status": r.get("status"),
                "detail": r.get("body")}
