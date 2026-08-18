"""free5GC core adapter (docker-compose deployment).

First target core, the free5GC + UERANSIM end-to-end is already validated on this VM
(UE registered, PDU session up, ping through gtp5g; see docs/free5gc-ueransim-e2e-guide.md).

Phase 2.0: connect-only. ``describe`` reports which NF containers are Up and their images;
``nf_endpoint`` resolves each NF's SBI address from the config (or docker inspect). Subscriber
provisioning drives the free5GC webui/Mongo (free5GC does NOT auto-add a subscriber, the
classic gotcha), left as a Phase-2.1 hook.
"""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
from typing import Any

from cpbench.adapters.base import CoreAdapter


def _valid_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s.strip())
        return True
    except ValueError:
        return False

# free5GC docker-compose service names, in NF order.
_NF_CONTAINERS = {
    "amf": "amf", "smf": "smf", "nrf": "nrf", "ausf": "ausf", "udm": "udm",
    "udr": "udr", "pcf": "pcf", "nssf": "nssf", "upf": "upf",
}


class Adapter(CoreAdapter):
    name = "free5gc"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.core.extra
        self.docker = e.get("docker", "docker")
        self.compose_dir = e.get("compose_dir", "~/free5gc-compose")
        self.use_sudo = bool(e.get("sudo", True))

    def _docker(self, *args: str) -> subprocess.CompletedProcess:
        # `sudo -n` = non-interactive: never block on a password prompt (fails fast instead),
        # and stdin=DEVNULL so a docker/sudo call can never hang a headless run.
        cmd = (["sudo", "-n"] if self.use_sudo else []) + [self.docker, *args]
        self.store.record_command(" ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=20)

    def describe(self) -> dict[str, Any]:
        facts: dict[str, Any] = {"core": "free5GC", "adapter": self.name,
                                 "mode": "control-plane"}
        if not shutil.which(self.docker) and not self.use_sudo:
            facts["note"] = "docker not found; connect-only facts unavailable"
            return facts
        up: list[str] = []
        for nf, cont in _NF_CONTAINERS.items():
            r = self._docker("ps", "--filter", f"name={cont}", "--format", "{{.Names}} {{.Status}}")
            if r.returncode == 0 and r.stdout.strip():
                up.append(nf)
        facts["nfs_up"] = ", ".join(up) if up else "(none detected / docker unreachable)"
        return facts

    def nf_endpoint(self, nf: str) -> str:
        ep = self.cfg.core.endpoints.get(nf, "")
        if ep:
            return ep
        # Try docker inspect for the container IP (SBI default port 8000).
        cont = _NF_CONTAINERS.get(nf, nf)
        r = self._docker("inspect", "-f",
                         "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", cont)
        ip = r.stdout.strip() if r.returncode == 0 else ""
        # docker prints the literal "invalid IP" (rc=0) for a missing container on some
        # versions, so validate before trusting it, a bogus endpoint must not look resolved.
        return f"{ip}:8000" if _valid_ip(ip) else ""

    def provision_subscribers(self, subscribers: list[dict[str, Any]]) -> dict[str, Any]:
        """Provision each subscriber into free5GC via the WebUI REST API (login admin/free5gc).
        Idempotent: an already-present subscriber (HTTP 409) counts as provisioned. free5GC
        ships zero subscribers, so without this 5G-AKA cannot succeed (the classic gotcha)."""
        if not subscribers:
            return {"provisioned": 0, "note": "no subscribers in config"}
        import json
        import urllib.error
        import urllib.request
        base = self.cfg.core.extra.get("webui", "http://localhost:5000")

        def _req(method, path, body=None, token=None):
            data = json.dumps(body).encode() if body is not None else None
            r = urllib.request.Request(base + path, data=data, method=method)
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
            return {"provisioned": 0, "error": f"webui login failed: {st} {out[:120]}"}
        token = json.loads(out).get("access_token")
        done = 0
        for sub in subscribers:
            payload = _subscriber_payload(sub)
            plmn = sub.get("plmn", "20893")
            ue = sub["supi"]
            self.store.record_command(f"[webui] POST /api/subscriber/{ue}/{plmn}")
            st, out = _req("POST", f"/api/subscriber/{ue}/{plmn}", payload, token)
            if st in (200, 201, 409):     # created or already-present
                done += 1
        return {"provisioned": done, "requested": len(subscribers)}

    def nf_alive(self, nf: str) -> bool | None:
        cont = _NF_CONTAINERS.get(nf, nf)
        r = self._docker("ps", "--filter", f"name={cont}", "--filter", "status=running",
                         "--format", "{{.Names}}")
        if r.returncode != 0:
            return None
        return bool(r.stdout.strip())

    def nf_log_grep(self, nf: str, patterns: list[str], tail: int = 800) -> bool | None:
        """Whether the NF's recent container logs contain any of ``patterns`` (case-insensitive).
        Used by logging-assurance tests (e.g. SMF-SEC-03). None if logs can't be read."""
        cont = _NF_CONTAINERS.get(nf, nf)
        r = self._docker("logs", "--tail", str(tail), cont)
        if r.returncode != 0:
            return None
        blob = (r.stdout + r.stderr).lower()
        return any(p.lower() in blob for p in patterns)


def _subscriber_payload(sub: dict[str, Any]) -> dict[str, Any]:
    """Build the free5GC WebUI subscriber document from a compact config entry
    (supi/ki/opc/dnn). Values not given fall back to the validated defaults from
    docs/free5gc-ueransim-e2e-guide.md."""
    ue = sub["supi"]
    plmn = sub.get("plmn", "20893")
    k = sub["ki"]
    opc = sub["opc"]
    amf = sub.get("amf", "8000")
    sst = int(sub.get("sst", 1))
    sd = sub.get("sd", "010203")
    dnn = sub.get("dnn", "internet")
    sqn = sub.get("sqn", "000000000000")
    snssai_key = f"{sst:02x}{sd}"
    return {
        "plmnID": plmn, "ueId": ue,
        "AuthenticationSubscription": {
            "authenticationManagementField": amf, "authenticationMethod": "5G_AKA",
            "milenage": {"op": {"encryptionAlgorithm": 0, "encryptionKey": 0, "opValue": ""}},
            "opc": {"encryptionAlgorithm": 0, "encryptionKey": 0, "opcValue": opc},
            "permanentKey": {"encryptionAlgorithm": 0, "encryptionKey": 0, "permanentKeyValue": k},
            "sequenceNumber": sqn},
        "AccessAndMobilitySubscriptionData": {
            "gpsis": ["msisdn-0900000000"],
            "nssai": {"defaultSingleNssais": [{"sst": sst, "sd": sd}],
                      "singleNssais": [{"sst": sst, "sd": sd}]},
            "subscribedUeAmbr": {"downlink": "2 Gbps", "uplink": "1 Gbps"}},
        "SessionManagementSubscriptionData": [{
            "singleNssai": {"sst": sst, "sd": sd},
            "dnnConfigurations": {dnn: {
                "pduSessionTypes": {"defaultSessionType": "IPV4", "allowedSessionTypes": ["IPV4"]},
                "sscModes": {"defaultSscMode": "SSC_MODE_1", "allowedSscModes": ["SSC_MODE_1"]},
                "5gQosProfile": {"5qi": 9, "arp": {"priorityLevel": 8, "preemptCap": "",
                                                    "preemptVuln": ""}, "priorityLevel": 8},
                "sessionAmbr": {"downlink": "1000 Mbps", "uplink": "1000 Mbps"}}}}],
        "SmfSelectionSubscriptionData": {
            "subscribedSnssaiInfos": {snssai_key: {"dnnInfos": [{"dnn": dnn}]}}},
        "AmPolicyData": {"subscCats": ["free5gc"]},
        "SmPolicyData": {"smPolicySnssaiData": {snssai_key: {
            "snssai": {"sst": sst, "sd": sd}, "smPolicyDnnData": {dnn: {"dnn": dnn}}}}},
        "FlowRules": [], "QosFlows": [], "ChargingDatas": [],
    }
