"""free5GC core adapter for a Kubernetes deployment (towards5gs-helm style).

Same contract as the docker ``free5gc`` adapter, but everything is resolved through
``kubectl`` instead of ``docker``:
  - ``nf_endpoint``  -> the NF's SBI Service ClusterIP:port (reachable from the node/host)
  - ``nf_alive``     -> the NF pod's phase == Running
  - ``nf_log_grep``  -> ``kubectl logs`` of the NF pod
  - ``describe``     -> which NF pods are Running
  - ``provision_subscribers`` -> the WebUI NodePort REST API

NF service names follow the helm chart's convention (``<prefix>-<nf>-service`` on 8080), except
the NRF which the chart exposes as ``nrf-nnrf`` on 8000. Both are overridable in the config.
"""
from __future__ import annotations

import subprocess
from typing import Any

from cpbench.adapters.base import CoreAdapter

_NFS = ("amf", "smf", "nrf", "ausf", "udm", "udr", "pcf", "nssf")


class Adapter(CoreAdapter):
    name = "free5gc_k8s"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.core.extra
        self.kubectl = e.get("kubectl", "kubectl")
        self.namespace = e.get("namespace", "free5gc")
        self.kubeconfig = e.get("kubeconfig", "")
        self.svc_prefix = e.get("svc_prefix", "free5gc-helm-free5gc")
        self.nf_label = e.get("nf_label", "app.kubernetes.io/name")
        # the chart labels pods app.kubernetes.io/name=free5gc-<nf> (not bare <nf>)
        self.nf_label_prefix = e.get("nf_label_prefix", "free5gc-")
        # per-NF SBI service (name, port); NRF differs from the rest in this chart
        self.services = e.get("services", {}) or {}
        self.webui = e.get("webui", "http://localhost:30500")

    def _kubectl(self, *args: str) -> subprocess.CompletedProcess:
        cmd = [self.kubectl]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        cmd += ["-n", self.namespace, *args]
        self.store.record_command(" ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)

    def _svc(self, nf: str) -> tuple[str, int]:
        if nf in self.services:
            s = self.services[nf]
            return s["name"], int(s.get("port", 8080))
        if nf == "nrf":
            return "nrf-nnrf", 8000
        return f"{self.svc_prefix}-{nf}-service", 8080

    def describe(self) -> dict[str, Any]:
        facts: dict[str, Any] = {"core": "free5GC (k8s)", "adapter": self.name,
                                 "mode": "control-plane", "namespace": self.namespace}
        up: list[str] = []
        for nf in _NFS:
            if self.nf_alive(nf):
                up.append(nf)
        facts["nfs_up"] = ", ".join(up) if up else "(none detected / cluster unreachable)"
        return facts

    def nf_endpoint(self, nf: str) -> str:
        ep = self.cfg.core.endpoints.get(nf, "")
        if ep:
            return ep
        svc, port = self._svc(nf)
        r = self._kubectl("get", "svc", svc, "-o", "jsonpath={.spec.clusterIP}")
        ip = r.stdout.strip() if r.returncode == 0 else ""
        return f"{ip}:{port}" if ip and ip != "None" else ""

    def _pods_phase(self, nf: str) -> list[str]:
        r = self._kubectl("get", "pods", "-l", f"{self.nf_label}={self.nf_label_prefix}{nf}",
                          "-o", "jsonpath={.items[*].status.phase}")
        if r.returncode != 0:
            return []
        return r.stdout.split()

    def nf_alive(self, nf: str) -> bool | None:
        phases = self._pods_phase(nf)
        if not phases:
            return None
        return any(p == "Running" for p in phases)

    def nf_log_grep(self, nf: str, patterns: list[str], tail: int = 800) -> bool | None:
        r = self._kubectl("logs", "-l", f"{self.nf_label}={self.nf_label_prefix}{nf}", "--tail", str(tail))
        if r.returncode != 0 or not r.stdout:
            return None
        blob = (r.stdout + r.stderr).lower()
        return any(p.lower() in blob for p in patterns)

    def provision_subscribers(self, subscribers: list[dict[str, Any]]) -> dict[str, Any]:
        """Provision via the WebUI REST API (NodePort). Same payload as the docker adapter."""
        if not subscribers:
            return {"provisioned": 0, "note": "no subscribers in config"}
        import json
        import urllib.error
        import urllib.request
        from cpbench.adapters.free5gc import _subscriber_payload

        def _req(method, path, body=None, token=None):
            data = json.dumps(body).encode() if body is not None else None
            r = urllib.request.Request(self.webui + path, data=data, method=method)
            r.add_header("Content-Type", "application/json")
            if token:
                r.add_header("Token", token)
            try:
                with urllib.request.urlopen(r, timeout=15) as resp:
                    return resp.status, resp.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()
            except Exception as e:  # noqa: BLE001
                return None, str(e)

        st, out = _req("POST", "/api/login", {"username": "admin", "password": "free5gc"})
        if st != 200:
            return {"provisioned": 0, "error": f"webui login failed: {st} {str(out)[:120]}"}
        token = json.loads(out).get("access_token")
        done = 0
        for sub in subscribers:
            st, _ = _req("POST", f"/api/subscriber/{sub['supi']}/{sub.get('plmn','20893')}",
                         _subscriber_payload(sub), token)
            if st in (200, 201, 409):
                done += 1
        return {"provisioned": done, "requested": len(subscribers)}
