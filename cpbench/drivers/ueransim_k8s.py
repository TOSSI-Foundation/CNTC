"""UERANSIM driver for an in-cluster (Kubernetes) deployment.

The helm free5GC ships its own ``ueransim-gnb`` + ``ueransim-ue`` pods, already attached. Two
modes:

* **observe-only** (default): read the running UE/gNB pod logs for the NGAP/NAS milestones and
  ``kubectl exec`` a ping through the UE TUN — non-disruptive. Procedures it can't see this way
  (release/deregister, wire-captured NAS/PFCP) grade 'na', never a fake pass.
* **drive** (``drivers.k8s_drive: true``): capture N2/N4 on the node, force a fresh UE attach
  (restart the UE pod) so the 5G-AKA + NAS-security handshake + N4 PFCP happen inside the
  capture window, drive UE-initiated release + deregistration via ``nr-cli``, decode the
  capture with tshark, then restart the UE pod to restore the deployment.

Either way it returns the same observation dict as the native ``ueransim`` driver, so the
AMF/SMF/AUSF/UDM test cases are unchanged.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from cpbench.drivers.base import Driver as BaseDriver

_OBS_CACHE: dict[str, dict] = {}
_BADAUTH_CACHE: dict[str, dict] = {}


class Driver(BaseDriver):
    name = "ueransim_k8s"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.core.extra
        self.kubectl = e.get("kubectl", "kubectl")
        self.namespace = e.get("namespace", "free5gc")
        self.kubeconfig = e.get("kubeconfig", "")
        self.ue_match = cfg.drivers.get("ue_pod_match", "ueransim-ue")
        self.gnb_match = cfg.drivers.get("gnb_pod_match", "ueransim-gnb")
        self.drive = bool(cfg.drivers.get("k8s_drive", False))
        self.cli = cfg.drivers.get("nr_cli_path", "/ueransim/nr-cli")
        self.supi = (cfg.subscribers[0]["supi"] if cfg.subscribers else "imsi-208930000000001")
        # capture N2+N4 on the node; 'any' catches the calico veths carrying pod traffic
        self.cap_iface = cfg.drivers.get("n2_iface", "any")
        self.sudo = ["sudo", "-n"]

    def capabilities(self) -> set[str]:
        return {"observe_registration"}

    # --- kubectl plumbing -----------------------------------------------------
    def _kubectl(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        cmd = [self.kubectl]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        cmd += ["-n", self.namespace, *args]
        self.store.record_command(" ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)

    def _pod(self, match: str) -> str:
        r = self._kubectl("get", "pods", "-o", "name")
        if r.returncode != 0:
            return ""
        for line in r.stdout.splitlines():
            name = line.split("/", 1)[-1]
            if match in name and "Terminating" not in line:
                return name
        return ""

    def _logs(self, pod: str, tail: int = 400) -> str:
        if not pod:
            return ""
        r = self._kubectl("logs", pod, "--tail", str(tail), timeout=30)
        return r.stdout if r.returncode == 0 else ""

    def _exec(self, pod: str, node: str, cmd: str):
        self.store.record_command(f"kubectl exec {pod} -- {self.cli} {node} --exec '{cmd}'")
        return self._kubectl("exec", pod, "--", self.cli, node, "--exec", cmd)

    # --- entry point ----------------------------------------------------------
    def observe_registration(self) -> dict[str, Any]:
        if _OBS_CACHE.get(self.namespace) is not None:
            return _OBS_CACHE[self.namespace]
        obs = self._drive_and_capture() if self.drive else self._observe_only()
        _OBS_CACHE[self.namespace] = obs
        return obs

    def observe_bad_auth(self) -> dict[str, Any]:
        """Negative attach: run an in-cluster ``nr-ue`` with an INVALID key alongside the
        deployment's UE, and confirm the network denies it (no auth bypass). Returns
        {denied, auth_failure, registered}. Cached process-wide.

        We copy the pod's own UE config, overwrite only the key with an all-zero key, and run
        ``nr-ue`` for a short window inside the UE pod — non-disruptive to the running good UE."""
        if _BADAUTH_CACHE.get(self.namespace) is not None:
            return _BADAUTH_CACHE[self.namespace]
        ue = self._pod(self.ue_match)
        if not ue:
            out = {"error": "no in-cluster UE pod"}
            _BADAUTH_CACHE[self.namespace] = out
            return out
        ue_bin = self.cli.rsplit("/", 1)[0] + "/nr-ue"
        # copy the pod's UE config, overwrite only the key, run nr-ue and capture the FULL NAS log
        # (the wrong-key UE never registers, so it retries for the whole window — the MAC failure
        # lands on the first attempt once the radio link is up).
        script = (
            "CFG=$(ls /ueransim/config/ue-config.yaml /ueransim/config/*ue*.yaml 2>/dev/null "
            "| grep -v cpbench-bad | head -1); "
            "sed 's/^key:.*/key: \"00000000000000000000000000000000\"/' \"$CFG\" "
            "> /tmp/cpbench-bad-ue.yaml; "
            f"timeout 22 {ue_bin} -c /tmp/cpbench-bad-ue.yaml 2>&1")
        auth_failure = rejected = registered = False
        # retry once if the attach never reached a decision (in-cluster radio link can be racy)
        for _ in range(2):
            self.store.record_command(f"kubectl exec {ue} -- sh -c '<wrong-key nr-ue attach>'")
            r = self._kubectl("exec", ue, "--", "sh", "-c", script, timeout=35)
            low = ((r.stdout or "") + (r.stderr or "")).lower()
            auth_failure = ("mac_failure" in low or "mac failure" in low
                            or "authentication failure" in low)
            rejected = "authentication reject" in low
            registered = "initial registration is successful" in low
            if registered or auth_failure or rejected:
                break   # got a definitive outcome
        if registered:                       # served without valid credentials = a real bypass
            out = {"denied": False, "auth_failure": False, "registered": True}
        elif auth_failure or rejected:        # network refused the wrong-key UE
            out = {"denied": True, "auth_failure": True, "registered": False}
        else:                                 # never reached auth -> can't judge (na, not a fail)
            out = {"error": "wrong-key attach did not reach the authentication step "
                            "(no MAC-failure and no registration observed)"}
        _BADAUTH_CACHE[self.namespace] = out
        return out

    def _parse_logs(self, ul: str, gl: str, obs: dict) -> None:
        obs["registered"] = "Initial Registration is successful" in ul
        obs["auth_request"] = "Authentication Request received" in ul
        obs["security_mode"] = "Security Mode Command received" in ul
        obs["pdu_session"] = "PDU Session establishment is successful" in ul
        m = re.search(r"uesimtun0,\s*([0-9.]+)", ul)
        obs["ue_ip"] = m.group(1) if m else ""
        obs["ng_setup"] = "NG Setup procedure is successful" in gl
        obs["initial_context_setup"] = "Initial Context Setup Request received" in gl
        obs["initial_nas_message"] = "Initial NAS message received from UE" in gl

    def _observe_only(self) -> dict[str, Any]:
        obs: dict[str, Any] = {"observe_only": True}
        try:
            ue, gnb = self._pod(self.ue_match), self._pod(self.gnb_match)
            if not ue or not gnb:
                obs["error"] = f"in-cluster UERANSIM pods not found (ue={ue!r}, gnb={gnb!r})"
                return obs
            self._parse_logs(self._logs(ue), self._logs(gnb), obs)
            obs["ping_ok"], obs["ping_detail"] = self._ping(ue, obs.get("ue_ip", ""))
            for k in ("deregistered", "pdu_released", "second_session", "ue_context_release"):
                obs[k] = None
            obs["nas_capture"] = obs["n4_capture"] = False
            obs["ok"] = bool(obs.get("registered"))
        except Exception as e:  # noqa: BLE001
            obs["error"] = f"{type(e).__name__}: {e}"
        return obs

    def _drive_and_capture(self) -> dict[str, Any]:
        obs: dict[str, Any] = {}
        pcap = self.store.raw / "k8s_wire.pcap"
        cap = self._start_capture(pcap)
        try:
            gnb = self._pod(self.gnb_match)
            # force a fresh UE attach so the 5G-AKA + NAS-security + N4 handshake is captured
            old = self._pod(self.ue_match)
            if old:
                self._kubectl("delete", "pod", old, "--wait=false")
            ue = self._await_registration()
            if not ue:
                obs["error"] = "fresh UE did not register within timeout"
            else:
                self._parse_logs(self._logs(ue), self._logs(gnb), obs)
                obs["ping_ok"], obs["ping_detail"] = self._ping(ue, obs.get("ue_ip", ""))
                # UE-initiated release + deregistration (captured -> N4 delete + UE ctx release).
                # Poll for each milestone (in-cluster log timing is variable under load) instead
                # of a single fixed-sleep check, so these don't flap PASS/FAIL on a timing race.
                self._exec(ue, self.supi, "ps-release-all")
                obs["pdu_released"] = self._wait_log(ue, "PDU Session Release Command received", 15)
                self._exec(ue, self.supi, "deregister normal")
                obs["deregistered"] = self._wait_log(ue, "De-registration is successful", 20)
                obs["ue_context_release"] = self._wait_log(gnb, "UE Context Release Command received", 20, tail=200)
                obs["second_session"] = None
        except Exception as e:  # noqa: BLE001
            obs["error"] = f"{type(e).__name__}: {e}"
        finally:
            self._stop_capture(cap)
        obs.update(self._decode_nas_security(pcap))
        obs.update(self._decode_pfcp(pcap))
        obs.update(self._decode_suci(pcap))
        obs["ok"] = bool(obs.get("registered"))
        # restore the deployment's UE
        cur = self._pod(self.ue_match)
        if cur:
            self._kubectl("delete", "pod", cur, "--wait=false")
        return obs

    def _wait_log(self, pod: str, needle: str, timeout: float, tail: int = 100) -> bool:
        """Poll a pod's logs until ``needle`` appears or ``timeout`` elapses (in-cluster log
        timing is variable, so a single fixed-sleep check flaps). Returns True once seen."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in self._logs(pod, tail):
                return True
            time.sleep(2)
        return False

    def _await_registration(self, timeout: float = 75.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ue = self._pod(self.ue_match)
            if ue and "Initial Registration is successful" in self._logs(ue, 40):
                time.sleep(2)   # let the PDU session + TUN settle
                return ue
            time.sleep(3)
        return self._pod(self.ue_match)

    # --- node capture + decode (host tcpdump/tshark) --------------------------
    def _start_capture(self, pcap: Path):
        if not shutil.which("tcpdump"):
            return None
        flt = "sctp or udp port 8805"
        self.store.record_command(f"{' '.join(self.sudo)} tcpdump -i {self.cap_iface} -w {pcap} '{flt}'")
        try:
            p = subprocess.Popen([*self.sudo, "tcpdump", "-i", self.cap_iface, "-w", str(pcap), flt],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL)
            time.sleep(1.0)
            return p
        except Exception:  # noqa: BLE001
            return None

    def _stop_capture(self, cap) -> None:
        if cap is None:
            return
        subprocess.run([*self.sudo, "pkill", "-f", f"tcpdump.*{self.cap_iface}"],
                       capture_output=True, stdin=subprocess.DEVNULL)
        try:
            cap.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass

    def _run(self, *args, timeout=40):
        return subprocess.run([*self.sudo, *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)

    def _decode_nas_security(self, pcap: Path) -> dict[str, Any]:
        if not shutil.which("tshark") or not pcap.exists():
            return {"nas_capture": False}
        r = self._run("tshark", "-r", str(pcap), "-Y", "ngap",
                      "-T", "fields", "-e", "nas_5gs.security_header_type")
        types = {int(t) for line in r.stdout.splitlines() for t in line.replace(",", " ").split() if t.isdigit()}
        if not types:
            return {"nas_capture": False}
        return {"nas_capture": True, "nas_sec_types": sorted(types),
                "nas_integrity": any(t in (1, 2, 3, 4) for t in types),
                "nas_ciphered": any(t in (2, 4) for t in types)}

    def _decode_pfcp(self, pcap: Path) -> dict[str, Any]:
        if not shutil.which("tshark") or not pcap.exists():
            return {"n4_capture": False}
        r = self._run("tshark", "-r", str(pcap), "-Y", "pfcp",
                      "-T", "fields", "-e", "pfcp.msg_type")
        types = {int(t) for line in r.stdout.splitlines() for t in line.replace(",", " ").split() if t.isdigit()}
        if not types:
            return {"n4_capture": False}
        return {"n4_capture": True, "n4_msg_types": sorted(types),
                "n4_session_establish": 50 in types, "n4_session_delete": 54 in types}

    def _decode_suci(self, pcap: Path) -> dict[str, Any]:
        if not shutil.which("tshark") or not pcap.exists():
            return {}
        r = self._run("tshark", "-r", str(pcap), "-Y", "nas_5gs.mm.message_type == 0x41", "-V")
        txt = r.stdout
        if "Protection scheme Id" not in txt:
            return {}
        m = re.search(r"Protection scheme Id:\s*(.+)", txt)
        scheme = m.group(1).strip() if m else "?"
        return {"suci_scheme": scheme, "supi_concealed": "NULL scheme" not in scheme}

    def _ping(self, ue_pod: str, ue_ip: str) -> tuple[bool, str]:
        if not ue_ip:
            return False, "no UE IP"
        r = self._kubectl("exec", ue_pod, "--", "ping", "-I", "uesimtun0",
                          "-c", "3", "-W", "2", "8.8.8.8", timeout=20)
        out = r.stdout + r.stderr
        ok = "0% packet loss" in out
        m = re.search(r"(\d+ received)", out)
        return ok, (m.group(1) if m else out.strip()[-120:])
