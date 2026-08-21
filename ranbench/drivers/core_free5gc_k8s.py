"""free5GC on Kubernetes as the RAN's core peer.

The core is not the subject of a RAN certificate, it is the peer the gNB needs in order to be
exercised at all: an AMF to terminate N2 and a UPF to terminate N3. This driver only does what
the RAN measurement requires of it, which is to confirm the AMF is reachable and to make sure
the SIM the UE will present is actually provisioned. Everything else about the core is the
tester's business.

Provisioning matters more than it looks. A core that has been redeployed comes up with an empty
subscriber database, and the symptom is not an obvious error: the UE attaches, 5G-AKA fails, and
every test downstream of registration records "the attach never completed" without saying why.
"""
from __future__ import annotations

import json
import socket
import subprocess
import urllib.error
import urllib.request
from typing import Any

from ranbench.drivers.base import Driver as BaseDriver

NGAP_SCTP_PORT = 38412


class Driver(BaseDriver):
    name = "core_free5gc_k8s"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.core.extra
        self.kubectl = e.get("kubectl", "kubectl")
        self.namespace = e.get("namespace", "free5gc")
        self.webui = e.get("webui", "http://localhost:30500")
        self.amf_svc = e.get("amf_n2_service", "")

    def capabilities(self) -> set[str]:
        return {"provision_subscribers", "amf_reachable"}

    # --- plumbing -------------------------------------------------------------
    def _kubectl(self, *args: str, timeout: int = 30) -> str:
        cmd = [self.kubectl, "-n", self.namespace, *args]
        self.store.record_command(" ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return r.stdout.strip() if r.returncode == 0 else ""

    def amf_n2_endpoint(self) -> str:
        """The AMF's N2 address, resolved live from the cluster."""
        svc = self.amf_svc or self._kubectl(
            "get", "svc", "-o",
            "jsonpath={range .items[?(@.spec.ports[0].protocol=='SCTP')]}{.metadata.name}{end}")
        if not svc:
            return ""
        ip = self._kubectl("get", "svc", svc, "-o", "jsonpath={.spec.clusterIP}")
        return f"{ip}:{NGAP_SCTP_PORT}" if ip and ip != "None" else ""

    def amf_reachable(self) -> bool | None:
        ep = self.amf_n2_endpoint()
        if not ep or ":" not in ep:
            return None
        host, _, port = ep.partition(":")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 132)
            s.settimeout(4)
            s.connect((host, int(port)))
            s.close()
            return True
        except OSError:
            return False

    # --- subscribers ----------------------------------------------------------
    def _webui(self, method: str, path: str, body=None, token: str | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.webui + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Token", token)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except Exception as e:  # noqa: BLE001
            return None, str(e)

    def provision_subscribers(self, subscribers: list[dict[str, Any]]) -> dict[str, Any]:
        """Ensure each SIM exists in the core, via the WebUI API.

        Already-present subscribers come back as a conflict, which counts as success: the point
        is that the SIM is there, not that this run created it.
        """
        if not subscribers:
            return {"provisioned": 0, "note": "no subscribers in config"}
        st, out = self._webui("POST", "/api/login",
                              {"username": "admin", "password": "free5gc"})
        if st != 200:
            return {"provisioned": 0,
                    "error": f"WebUI login failed ({st}) at {self.webui}: {str(out)[:120]}. "
                             f"5G-AKA will fail unless the SIM is already provisioned."}
        token = json.loads(out).get("access_token")
        done, failed = 0, []
        for sub in subscribers:
            supi = sub.get("supi", "")
            plmn = str(sub.get("plmn", "20893"))
            st, body = self._webui("POST", f"/api/subscriber/{supi}/{plmn}",
                                   _payload(sub), token)
            if st in (200, 201, 409):
                done += 1
            else:
                failed.append(f"{supi}: HTTP {st}")
        out = {"provisioned": done, "requested": len(subscribers)}
        if failed:
            out["failed"] = failed
        return out


def _payload(sub: dict) -> dict:
    """The free5GC WebUI subscriber document for one SIM."""
    supi = sub.get("supi", "")
    sst = int(sub.get("sst", 1))
    sd = str(sub.get("sd", "010203"))
    dnn = sub.get("dnn", "internet")
    return {
        "userNumber": 1,
        "ueId": supi,
        "plmnID": str(sub.get("plmn", "20893")),
        "AuthenticationSubscription": {
            "authenticationManagementField": "8000",
            "authenticationMethod": "5G_AKA",
            "milenage": {"op": {"encryptionAlgorithm": 0, "encryptionKey": 0,
                                "opValue": ""}},
            "opc": {"encryptionAlgorithm": 0, "encryptionKey": 0,
                    "opcValue": sub.get("opc", "")},
            "permanentKey": {"encryptionAlgorithm": 0, "encryptionKey": 0,
                             "permanentKeyValue": sub.get("ki", "")},
            "sequenceNumber": "16f3b3f70fc2",
        },
        "AccessAndMobilitySubscriptionData": {
            "gpsis": ["msisdn-0900000000"],
            "nssai": {"defaultSingleNssais": [{"sst": sst, "sd": sd}],
                      "singleNssais": [{"sst": sst, "sd": sd}]},
            "subscribedUeAmbr": {"downlink": "2 Gbps", "uplink": "1 Gbps"},
        },
        "SessionManagementSubscriptionData": [{
            "singleNssai": {"sst": sst, "sd": sd},
            "dnnConfigurations": {dnn: {
                "pduSessionTypes": {"defaultSessionType": "IPV4",
                                    "allowedSessionTypes": ["IPV4"]},
                "sscModes": {"defaultSscMode": "SSC_MODE_1",
                             "allowedSscModes": ["SSC_MODE_2", "SSC_MODE_3"]},
                "5gQosProfile": {"5qi": 9, "arp": {"priorityLevel": 8,
                                 "preemptCap": "", "preemptVuln": ""}, "priorityLevel": 8},
                "sessionAmbr": {"uplink": "200 Mbps", "downlink": "100 Mbps"},
            }},
        }],
        "SmfSelectionSubscriptionData": {
            "subscribedSnssaiInfos": {f"{sst:02d}{sd}": {"dnnInfos": [{"dnn": dnn}]}}},
        "AmPolicyData": {"subscCats": ["free5gc"]},
        "SmPolicyData": {"smPolicySnssaiData": {f"{sst:02d}{sd}": {
            "snssai": {"sst": sst, "sd": sd},
            "smPolicyDnnData": {dnn: {"dnn": dnn}}}}},
        "FlowRules": [],
    }
