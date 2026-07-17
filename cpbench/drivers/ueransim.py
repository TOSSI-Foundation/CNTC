"""UERANSIM driver — drives N1/N2 (NAS + NGAP) through the AMF.

Wraps a native UERANSIM build (``nr-gnb`` + ``nr-ue``) to run a real registration +
PDU-session against the live core, then parses the gNB/UE logs into a structured observation
the AMF/SMF/AUSF/UDM test cases assert on. Validated topology: docs/free5gc-ueransim-e2e-guide.md
(NG Setup -> 5G-AKA -> Security Mode -> Registration -> PDU session -> data path).

The observation is cached process-wide (one real registration per `cpbench run`), so a
``--nf all`` run doesn't re-attach five times, and the AUSF/UDM suites can read the same
registration to verify their transitive role (a successful 5G-AKA proves AUSF+UDM worked).
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from cpbench.drivers.base import Driver as BaseDriver

_PROCEDURES = {
    "register", "authenticate", "security_mode", "pdu_establish", "ng_setup", "data_path",
}

# Process-wide caches: one real good/bad attach per `cpbench run`, keyed by amf_addr.
_OBS_CACHE: dict[str, dict] = {}
_BADAUTH_CACHE: dict[str, dict] = {}


class Driver(BaseDriver):
    name = "ueransim"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        d = cfg.drivers.get("ueransim_dir", "~/UERANSIM")
        self.dir = Path(d).expanduser()
        self.gnb_bin = self.dir / "build" / "nr-gnb"
        self.ue_bin = self.dir / "build" / "nr-ue"
        self.cli_bin = self.dir / "build" / "nr-cli"
        self.amf_n2 = cfg.drivers.get("amf_n2_addr", "10.100.200.16")
        self.link_ip = cfg.drivers.get("gnb_link_ip", "10.100.200.1")
        self.n2_iface = cfg.drivers.get("n2_iface", "br-free5gc")   # interface to capture N2 on
        self.sudo = ["sudo", "-n"]

    def capabilities(self) -> set[str]:
        return set(_PROCEDURES) if self.is_built() else set()

    def is_built(self) -> bool:
        return self.gnb_bin.exists() and self.ue_bin.exists()

    # --- config generation ----------------------------------------------------
    def _write_configs(self) -> tuple[Path, Path]:
        import yaml
        cfgdir = self.dir / "config"
        g = yaml.safe_load((cfgdir / "free5gc-gnb.yaml").read_text())
        g["linkIp"] = self.link_ip
        g["ngapIp"] = self.link_ip
        g["gtpIp"] = self.link_ip
        g["amfConfigs"] = [{"address": self.amf_n2, "port": 38412}]
        gpath = cfgdir / "cpbench-gnb.yaml"
        gpath.write_text(yaml.safe_dump(g, sort_keys=False))
        u = yaml.safe_load((cfgdir / "free5gc-ue.yaml").read_text())
        u["gnbSearchList"] = [self.link_ip]
        upath = cfgdir / "cpbench-ue.yaml"
        upath.write_text(yaml.safe_dump(u, sort_keys=False))
        self.supi = u.get("supi", "")
        return gpath, upath

    def _run(self, *args: str, timeout: int = 20) -> subprocess.CompletedProcess:
        return subprocess.run([*self.sudo, *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)

    def _kill(self) -> None:
        for b in ("nr-ue", "nr-gnb"):
            self._run("pkill", "-x", b, timeout=10)

    def _cli(self, node: str, cmd: str):
        """Drive a nr-cli command against a running UE/gNB node (needs sudo — the node's
        control socket is root-owned). Used for UE-initiated release/deregistration."""
        self.store.record_command(f"{' '.join(self.sudo)} {self.cli_bin} {node} --exec '{cmd}'")
        return self._run(str(self.cli_bin), node, "--exec", cmd, timeout=10)

    @staticmethod
    def _wait_for(log: Path, needle: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if log.exists() and needle.lower() in log.read_text(errors="ignore").lower():
                return True
            time.sleep(0.3)
        return False

    # --- the real attach ------------------------------------------------------
    def observe_registration(self) -> dict[str, Any]:
        """Run gNB + UE against the live core once; return a structured observation.
        Cached process-wide so repeated calls (across NFs) reuse the same real attach."""
        key = self.amf_n2          # one core + one UE per `cpbench run`
        if not self.is_built():
            return {"ok": False, "error": "UERANSIM not built", "not_implemented": True}
        cached = _OBS_CACHE.get(key)
        if cached is not None:
            return cached

        gnb_log = Path(self.store.raw / "ueransim-gnb.log")
        ue_log = Path(self.store.raw / "ueransim-ue.log")
        obs: dict[str, Any] = {"ok": False}
        try:
            gpath, upath = self._write_configs()
            self._kill()
            time.sleep(1)
            # start N2 (SCTP) capture for the AMF NAS-security SCAS checks (best-effort)
            pcap = self.store.raw / "n2.pcap"
            cap = self._start_capture(pcap)
            self.store.record_command(f"{' '.join(self.sudo)} {self.gnb_bin} -c {gpath}")
            with open(gnb_log, "w") as gl:
                gnb = subprocess.Popen([*self.sudo, str(self.gnb_bin), "-c", str(gpath)],
                                       stdout=gl, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            obs["sctp"] = self._wait_for(gnb_log, "SCTP connection established", 12)
            obs["ng_setup"] = self._wait_for(gnb_log, "NG Setup procedure is successful", 8)

            self.store.record_command(f"{' '.join(self.sudo)} {self.ue_bin} -c {upath}")
            with open(ue_log, "w") as ul:
                ue = subprocess.Popen([*self.sudo, str(self.ue_bin), "-c", str(upath)],
                                      stdout=ul, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            obs["auth_request"] = self._wait_for(ue_log, "Authentication Request received", 15)
            obs["security_mode"] = self._wait_for(ue_log, "Security Mode Command received", 6)
            obs["registered"] = self._wait_for(ue_log, "Initial Registration is successful", 8)
            obs["pdu_session"] = self._wait_for(ue_log, "PDU Session establishment is successful", 10)
            # the "TUN interface[uesimtun0, <ip>] is up" line lands a beat after PDU success
            self._wait_for(ue_log, "uesimtun0", 6)
            obs["ue_ip"] = self._parse_ue_ip(ue_log)
            time.sleep(1.0)                       # let the TUN route settle before pinging
            obs["ping_ok"], obs["ping_detail"] = self._ping(obs["ue_ip"])
            # A second PDU session (SMF-SESS-04) before releasing everything
            if obs.get("pdu_session"):
                self._cli(self.supi, "ps-establish")
                obs["second_session"] = self._wait_for(
                    ue_log, "PDU Session establishment is successful PSI[2]", 6)
            # UE-initiated PDU session release + deregistration (driven via nr-cli)
            if obs.get("registered"):
                self._cli(self.supi, "ps-release-all")
                obs["pdu_released"] = self._wait_for(
                    ue_log, "PDU Session Release Command received", 6)
                self._cli(self.supi, "deregister normal")
                obs["deregistered"] = self._wait_for(
                    ue_log, "De-registration is successful", 6)
                self._wait_for(gnb_log, "UE Context Release", 5)   # lands after deregistration
            # NGAP sub-procedure evidence observed on the gNB (registration + deregistration)
            gtxt = gnb_log.read_text(errors="ignore") if gnb_log.exists() else ""
            obs["initial_context_setup"] = "Initial Context Setup Request received" in gtxt
            obs["ue_context_release"] = "UE Context Release Command received" in gtxt
            obs["initial_nas_message"] = "Initial NAS message received from UE" in gtxt
            # stop the capture; decode NAS security (AMF-SEC-01/02) + N4 PFCP (SMF-N4-*)
            self._stop_capture(cap)
            obs.update(self._decode_nas_security(pcap))
            obs.update(self._decode_pfcp(pcap))
            obs["ok"] = bool(obs.get("registered"))
            obs["gnb_log"] = str(gnb_log)
            obs["ue_log"] = str(ue_log)
        except Exception as e:  # noqa: BLE001
            obs["error"] = f"{type(e).__name__}: {e}"
        finally:
            self._kill()
        _OBS_CACHE[key] = obs
        return obs

    def observe_bad_auth(self) -> dict[str, Any]:
        """Attach a UE with an INVALID key; the network must deny registration (no auth
        bypass). Returns {denied, auth_failure, registered}. Cached process-wide."""
        key = self.amf_n2
        if not self.is_built():
            return {"error": "UERANSIM not built"}
        if key in _BADAUTH_CACHE:
            return _BADAUTH_CACHE[key]
        import yaml
        gnb_log = Path(self.store.raw / "ueransim-badauth-gnb.log")
        ue_log = Path(self.store.raw / "ueransim-badauth-ue.log")
        obs: dict[str, Any] = {}
        try:
            gpath, _ = self._write_configs()
            cfgdir = self.dir / "config"
            u = yaml.safe_load((cfgdir / "cpbench-ue.yaml").read_text())
            u["key"] = "00000000000000000000000000000000"    # wrong K -> AUTN MAC failure
            badpath = cfgdir / "cpbench-ue-bad.yaml"
            badpath.write_text(yaml.safe_dump(u, sort_keys=False))
            self._kill()
            time.sleep(1)
            with open(gnb_log, "w") as gl:
                subprocess.Popen([*self.sudo, str(self.gnb_bin), "-c", str(gpath)],
                                 stdout=gl, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            self._wait_for(gnb_log, "NG Setup procedure is successful", 8)
            self.store.record_command(f"{' '.join(self.sudo)} {self.ue_bin} -c {badpath}  # invalid key")
            with open(ue_log, "w") as ul:
                subprocess.Popen([*self.sudo, str(self.ue_bin), "-c", str(badpath)],
                                 stdout=ul, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            obs["auth_failure"] = (self._wait_for(ue_log, "Authentication Failure", 8)
                                   or self._wait_for(ue_log, "Authentication Reject", 3))
            txt = ue_log.read_text(errors="ignore") if ue_log.exists() else ""
            obs["registered"] = "Initial Registration is successful" in txt
            obs["denied"] = not obs["registered"]
            obs["ue_log"] = str(ue_log)
        except Exception as e:  # noqa: BLE001
            obs["error"] = f"{type(e).__name__}: {e}"
        finally:
            self._kill()
        _BADAUTH_CACHE[key] = obs
        return obs

    # --- N2 wire capture for the AMF NAS-security SCAS checks -----------------
    def _start_capture(self, pcap: Path):
        if not shutil.which("tcpdump"):
            return None
        # Capture both N2 (SCTP/NGAP+NAS) and N4 (PFCP/UDP 8805) so one pcap feeds both the
        # AMF NAS-security checks and the SMF N4 checks.
        flt = "sctp or udp port 8805"
        self.store.record_command(f"{' '.join(self.sudo)} tcpdump -i {self.n2_iface} -w {pcap} '{flt}'")
        try:
            p = subprocess.Popen([*self.sudo, "tcpdump", "-i", self.n2_iface, "-w", str(pcap), flt],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL)
            time.sleep(1.0)   # let tcpdump bind before the SCTP association forms
            return p
        except Exception:  # noqa: BLE001
            return None

    def _stop_capture(self, cap) -> None:
        if cap is None:
            return
        self._run("pkill", "-f", f"tcpdump.*{self.n2_iface}", timeout=8)
        try:
            cap.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass

    def _decode_nas_security(self, pcap: Path) -> dict[str, Any]:
        """Decode the captured N2 with tshark: which NAS 5GS security header types appear.
        0=plain, 1=integrity, 2=integrity+ciphered, 3=integrity(new ctx), 4=integrity+ciphered(new ctx).
        After the Security Mode Command, messages must be integrity-protected (>=1) and ciphered (2/4)."""
        if not shutil.which("tshark") or not pcap.exists():
            return {"nas_capture": False}
        r = self._run("tshark", "-r", str(pcap), "-Y", "ngap",
                      "-T", "fields", "-e", "nas_5gs.security_header_type", timeout=40)
        types: set[int] = set()
        for line in r.stdout.splitlines():
            for tok in line.replace(",", " ").split():
                if tok.isdigit():
                    types.add(int(tok))
        if not types:
            return {"nas_capture": False}
        return {
            "nas_capture": True,
            "nas_sec_types": sorted(types),
            "nas_integrity": any(t in (1, 2, 3, 4) for t in types),
            "nas_ciphered": any(t in (2, 4) for t in types),
        }

    def _decode_pfcp(self, pcap: Path) -> dict[str, Any]:
        """Decode the captured N4 with tshark: which PFCP message types the SMF exchanged with
        the UPF. 50=Session Establishment Req, 51=Resp, 52=Modification Req, 54=Deletion Req."""
        if not shutil.which("tshark") or not pcap.exists():
            return {"n4_capture": False}
        r = self._run("tshark", "-r", str(pcap), "-Y", "pfcp",
                      "-T", "fields", "-e", "pfcp.msg_type", timeout=40)
        types: set[int] = set()
        for line in r.stdout.splitlines():
            for tok in line.replace(",", " ").split():
                if tok.isdigit():
                    types.add(int(tok))
        if not types:
            return {"n4_capture": False}
        return {
            "n4_capture": True,
            "n4_msg_types": sorted(types),
            "n4_session_establish": 50 in types,
            "n4_session_delete": 54 in types,
        }

    @staticmethod
    def _parse_ue_ip(log: Path) -> str:
        import re
        if not log.exists():
            return ""
        m = re.search(r"uesimtun0,\s*([0-9.]+)", log.read_text(errors="ignore"))
        return m.group(1) if m else ""

    def _ping(self, ue_ip: str) -> tuple[bool, str]:
        if not ue_ip:
            return False, "no UE IP"
        r = self._run("ping", "-I", "uesimtun0", "-c", "3", "-W", "2", "8.8.8.8", timeout=15)
        out = (r.stdout + r.stderr)
        ok = " 0% packet loss" in out
        import re
        m = re.search(r"(\d+) received", out)
        return ok, (m.group(0) if m else out.strip()[-120:])

    def teardown(self) -> None:
        # Leave the cache; only ensure no stray processes linger.
        try:
            self._kill()
        except Exception:  # noqa: BLE001
            pass
