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
import time
import urllib.error
import urllib.parse
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
        self.reset_amf = bool(e.get("reset_amf", True))

    def capabilities(self) -> set[str]:
        return {"provision_subscribers", "amf_reachable", "reset_ue_contexts",
                "subscriber_data_ready"}

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

    def _svc_ip(self, fragment: str) -> str:
        """ClusterIP of the first service whose name contains ``fragment``."""
        names = self._kubectl("get", "svc", "-o", "jsonpath={range .items[*]}{.metadata.name}\n{end}")
        svc = next((n for n in names.splitlines() if fragment in n), "")
        if not svc:
            return ""
        ip = self._kubectl("get", "svc", svc, "-o", "jsonpath={.spec.clusterIP}")
        return ip if ip and ip != "None" else ""

    def subscriber_data_ready(self, sub: dict[str, Any]) -> tuple[bool | None, str]:
        """Can the core actually serve this SIM's session-management subscription data?

        This is the exact query the SMF makes during PDU Session Establishment, so it is the
        cheapest way to find out in advance whether a session can succeed. It is worth doing
        because the failure is silent and expensive: free5GC's UDM holds a long-lived HTTP/2
        connection to the UDR that can go stale (observed after a day of uptime, with neither
        pod having restarted). The UDM then answers the SMF with a 500, the PDU session is
        refused, and the RAN run spends twelve minutes producing a measurement in which the
        data-path requirements are unjudgeable through no fault of the RAN.

        Returns (ready, detail); None means the probe itself could not be run, which is not
        evidence either way.
        """
        ip = self._svc_ip("udm")
        if not ip:
            return None, "no UDM service found; cannot check the subscriber data path"
        supi = sub.get("supi", "")
        plmn = str(sub.get("plmn", "20893"))
        mcc, mnc = plmn[:3], plmn[3:]
        nssai = json.dumps({"sst": int(sub.get("sst", 1)), "sd": str(sub.get("sd", "010203"))},
                           separators=(",", ":"))
        plmn_id = json.dumps({"mcc": mcc, "mnc": mnc}, separators=(",", ":"))
        url = (f"http://{ip}:8080/nudm-sdm/v2/{supi}/sm-data"
               f"?dnn={urllib.parse.quote(str(sub.get('dnn', 'internet')))}"
               f"&plmn-id={urllib.parse.quote(plmn_id)}"
               f"&single-nssai={urllib.parse.quote(nssai)}")
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return True, f"UDM serves sm-data for {supi}"
                return False, f"UDM returned HTTP {resp.status} for {supi}"
        except urllib.error.HTTPError as e:
            return False, (f"UDM returned HTTP {e.code} for {supi}. PDU sessions will be "
                           f"refused. If this is a 500, the UDM has most likely lost its "
                           f"connection to the UDR: "
                           f"kubectl -n {self.namespace} rollout restart "
                           f"deploy/<udr> deploy/<udm>")
        except Exception as e:  # noqa: BLE001
            return None, f"could not reach the UDM at {ip}:8080 ({type(e).__name__}: {e})"

    # --- registration state ---------------------------------------------------
    def reset_ue_contexts(self) -> dict[str, Any]:
        """Clear whatever the AMF still believes about the UE, before the RAN is started.

        This is the single largest source of run-to-run variance in RAN measurement, and it is
        not a RAN fault at all. free5GC's AMF can be left holding a UE context in
        DeregistrationInitiated after a previous campaign ends abruptly (an interrupted run, a
        UE killed mid-session). It then answers the next Registration Request with a reject, the
        attach never completes, and every requirement judged on that attach records 'na'. The
        run looks like a catastrophic RAN when nothing was measured.

        The AMF holds this state in memory, so restarting it is what clears it. That costs about
        a minute and buys a defined starting state, which is the trade a certification run
        should always make: the same input has to produce the same measurement.

        Skipped when ``core.reset_amf: false``, for testers who manage core state themselves.
        """
        if not self.reset_amf:
            return {"reset": False, "note": "disabled by core.reset_amf: false"}
        dep = self._kubectl("get", "deploy", "-o", "name", timeout=30)
        amf = next((d for d in dep.splitlines() if "amf" in d), "")
        if not amf:
            return {"reset": False, "error": "no AMF deployment found; a stale UE context "
                                             "would silently break the attach"}
        if not self._kubectl("rollout", "restart", amf, timeout=60):
            return {"reset": False, "error": f"could not restart {amf}"}
        self._kubectl("rollout", "status", amf, "--timeout=180s", timeout=200)
        # Ready is not the same as accepting N2: wait for the socket the CU-CP will dial.
        for _ in range(30):
            if self.amf_reachable() is True:
                return {"reset": True, "amf": amf.split("/")[-1]}
            time.sleep(2)
        return {"reset": True, "amf": amf.split("/")[-1],
                "warning": "AMF restarted but N2 is not accepting yet"}

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
