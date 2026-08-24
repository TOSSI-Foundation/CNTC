"""OAI nr-UE driver: drives the RAN over a ZeroMQ virtual radio and observes the attach.

This is the RAN's stimulus. Where ``cpbench`` pointed a UE at a core, here the RAN *is* the
subject, so the driver has to supply the UE below it and orchestrate the whole measurement:

  1. bring the three product classes up in order (CU-CP -> CU-UP -> DU), waiting on each
  2. run one real UE attach: cell search, RACH, RRC setup, registration, PDU session, data
  3. bring everything down again -- OCUDU only closes its pcaps at shutdown, so teardown is
     what makes the evidence readable
  4. decode the per-interface pcaps into the facts the test cases assert on

The observation is cached process-wide, so a ``--target all`` run attaches once and the CU-CP,
O-DU and CU-UP suites all read the same evidence rather than re-attaching three times.

The UE itself is an external process (OAI, built by the tester); this driver owns only its
invocation and the parsing of what it reported. Every parameter that must agree between the DU
and the UE is read from the campaign config, and a mismatch shows up as a failed attach rather
than a silently wrong measurement -- see docs/RANBENCH-RIG.md for the eight that matter.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

from ranbench.config import expand_user_path
from ranbench.adapters.ocudu import E1_SCTP_PORT, F1C_SCTP_PORT, NGAP_SCTP_PORT
from ranbench.drivers.base import Driver as BaseDriver

_PROCEDURES = {"attach", "register", "pdu_session", "data_path"}

# UE log needle -> observation key. These are the milestones of one attach, in order.
_MILESTONES = [
    ("UE synchronized", "synchronized"),
    ("SIB1 decoded", "sib1_decoded"),
    ("RA-Msg3 transmitted", "rach_completed"),
    ("Received NR_RRCSetup", "rrc_setup"),
    ("State = NR_RRC_CONNECTED", "rrc_connected"),
    ("FGS_AUTHENTICATION_REQUEST", "authentication"),
    ("FGS_REGISTRATION_ACCEPT", "registration_accept"),
    ("RegistrationComplete", "registration_complete"),
    ("PDU Session Establishment Accept", "pdu_session"),
    ("successfully configured", "tun_up"),
]

_UE_IP_RE = re.compile(r"UE IPv4:\s*([0-9.]+)")
_TUN_RE = re.compile(r"TUN Interface (\S+) successfully configured")

# One attach per `ranbench run`, keyed by the UE's identity.
_OBS_CACHE: dict[str, dict] = {}


class Driver(BaseDriver):
    name = "ue_oai_zmq"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        d = cfg.drivers
        self.bin = expand_user_path(d.get("ue_bin", "~/openairinterface5g/cmake_targets/ran_build/build/nr-uesoftmodem"))
        self.uecap = expand_user_path(d.get("uecap_file", "")) if d.get("uecap_file") else None
        self.prb = int(d.get("prb", 51))
        self.numerology = int(d.get("numerology", 1))
        self.band = int(d.get("band", 78))
        self.freq_hz = int(d.get("centre_freq_hz", 3489420000))
        self.ssb = int(d.get("ssb", 0))
        self.zmq_tx = d.get("zmq_tx", "tcp://127.0.0.1:4557")
        self.zmq_rx = d.get("zmq_rx", "tcp://127.0.0.1:4556")
        self.attach_timeout = float(d.get("attach_timeout_s", 150))
        self.ping_target = d.get("ping_target", "8.8.8.8")
        sub = (cfg.subscribers or [{}])[0]
        self.imsi = str(sub.get("supi", "")).replace("imsi-", "")
        self.key = sub.get("ki", "")
        self.opc = sub.get("opc", "")
        self.dnn = sub.get("dnn", "internet")
        self.sst = int(sub.get("sst", 1))
        self.sd = str(sub.get("sd", "")).strip()
        self.sudo = ["sudo", "-n"]

    def capabilities(self) -> set[str]:
        return set(_PROCEDURES) if self.is_built() else set()

    def is_built(self) -> bool:
        return Path(self.bin).exists()

    # --- process plumbing -----------------------------------------------------
    def _run(self, *args: str, timeout: int = 20) -> subprocess.CompletedProcess:
        return subprocess.run([*self.sudo, *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout, start_new_session=True)

    def _kill_ue(self) -> None:
        try:
            self._run("pkill", "-x", "nr-uesoftmodem", timeout=10)
        except Exception:  # noqa: BLE001
            pass

    def _argv(self) -> list[str]:
        argv = [str(self.bin),
                "-r", str(self.prb), "--numerology", str(self.numerology),
                "--band", str(self.band), "-C", str(self.freq_hz), "--ssb", str(self.ssb),
                "--uicc0.imsi", self.imsi, "--uicc0.key", self.key, "--uicc0.opc", self.opc,
                "--uicc0.pdu_sessions.[0].dnn", self.dnn,
                "--uicc0.pdu_sessions.[0].nssai_sst", str(self.sst)]
        if self.sd:
            # the config carries the S-NSSAI SD as the 3-byte hex string the core is provisioned
            # with; OAI wants it as a number
            sd = self.sd[2:] if self.sd.lower().startswith("0x") else self.sd
            argv += ["--uicc0.pdu_sessions.[0].nssai_sd", f"0x{sd}"]
        argv += ["--zmq.[0].tx_channels", self.zmq_tx,
                 "--zmq.[0].rx_channels", self.zmq_rx,
                 "--device.name", "oai_zmqdevif"]
        if self.uecap:
            # without a declared UE capability set OAI asserts (max_mimo_layers > 0) right
            # after RRC Setup, so this is not optional in practice
            argv += ["--uecap_file", str(self.uecap)]
        return argv

    @staticmethod
    def _log_has(log: Path, needle: str) -> bool:
        try:
            return log.exists() and needle.lower() in log.read_text(errors="ignore").lower()
        except OSError:
            return False

    # --- the measurement ------------------------------------------------------
    def observe_attach(self, ran, observer) -> dict[str, Any]:
        """Run one full attach against the RAN and return the decoded observation."""
        key = self.imsi or "ue"
        cached = _OBS_CACHE.get(key)
        if cached is not None:
            return cached
        obs: dict[str, Any] = {"ok": False}
        if not self.is_built():
            obs["error"] = f"OAI nr-UE not built at {self.bin}"
            _OBS_CACHE[key] = obs
            return obs

        ue_log = Path(self.store.raw / "oai-nr-ue.log")
        # One retry. An attach can stall for reasons outside the RAN, most often the core
        # still holding UE context from an earlier session, which makes it drop the new
        # Registration Request. Retrying once distinguishes that from a real RAN failure;
        # measuring the RAN against a half-finished attach would be worse than retrying.
        try:
            for attempt in (1, 2):
                obs["attempt"] = attempt
                self._bring_up(ran, obs)
                if obs.get("cell_active"):
                    self._attach(ue_log, obs)
                if obs.get("registration_accept"):
                    break
                if attempt == 1:
                    obs["retried"] = True
                    self._stop_all(ran)
                    time.sleep(10)
        except Exception as e:  # noqa: BLE001, a broken measurement is 'na', not a crash
            obs["error"] = f"{type(e).__name__}: {e}"
        finally:
            self._stop_all(ran)

        self._decode(ran, observer, obs)
        obs["ok"] = bool(obs.get("registration_accept"))
        _OBS_CACHE[key] = obs
        return obs

    def _stop_all(self, ran) -> None:
        """Stop the UE, then each product with SIGINT so it closes its pcaps, only then is the
        evidence complete. The UE is signalled rather than killed so it can deregister, leaving
        the core without stale UE context for the next campaign."""
        self._kill_ue()
        # The UE going away triggers UE Context Release across N2 and F1, and those procedures
        # are themselves part of the catalog. Wait for the CU-CP to actually log the release
        # rather than hoping a fixed sleep covers it, a flat delay made these cases flaky,
        # passing in one run and failing in the next.
        # The release cannot be watched for here: it is a CU-CP event, and that log stays
        # empty until the process exits. So allow a fixed settling window for the procedure to
        # complete on the wire, then stop the products, which is what flushes the pcaps the
        # release cases are actually judged from.
        time.sleep(8)
        for tgt in ("du", "cuup", "cucp"):
            try:
                ran.stop(tgt)
                time.sleep(2)
            except Exception:  # noqa: BLE001
                pass
        time.sleep(2)

    def _bring_up(self, ran, obs: dict) -> None:
        """Start CU-CP, CU-UP then O-DU, waiting for each to reach its milestone."""
        self._kill_ue()
        for tgt in ("du", "cuup", "cucp"):
            ran.stop(tgt)
        time.sleep(5)

        ran.start("cucp")
        # Wait for the F1-C listener to be BOUND before starting anything that dials it. The
        # O-DU and O-CU-UP are SCTP clients and neither retries: one "Connection refused" and
        # the product is gone, leaving a run that measures a stack which never assembled.
        obs["cucp_listening"] = ran.wait_for_listen(F1C_SCTP_PORT, 45)
        if not obs["cucp_listening"]:
            print("[ranbench] warning: the CU-CP is not listening on F1-C; the O-DU and O-CU-UP "
                  "will be refused. Check the CU-CP config and that nothing else holds the port.")
        # On the socket, not the log. The CU-CP writes nothing until it exits, so the log
        # answered "not connected" on every run no matter what N2 was doing.
        obs["ng_setup"] = ran.wait_for_association(NGAP_SCTP_PORT, 30)

        # E1 must actually establish, not merely be attempted. The CU-CP refuses to admit a UE
        # when it has no user-plane node: the RRC Setup Request arrives and it answers with a UE
        # Context Release instead of an RRC Setup, and the run then spends its whole attach
        # budget on a UE that can never be admitted.
        #
        # Judged on the socket, not on a log line. OCUDU buffers its logs, so an E1 association
        # that succeeded can still be invisible in the file when the poll gives up. The CU-UP is
        # given one restart because it can lose the race to the CU-CP's listener and does not
        # retry by itself. If E1 still does not come up the attach is attempted anyway and the
        # fact is recorded, because the NGAP and F1AP evidence is worth collecting either way.
        ran.start("cuup")
        obs["e1_setup"] = ran.wait_for_association(E1_SCTP_PORT, 45)
        if not obs["e1_setup"]:
            print("[ranbench] E1 has not come up; restarting the O-CU-UP once")
            ran.stop("cuup")
            time.sleep(4)
            ran.start("cuup")
            obs["e1_setup"] = ran.wait_for_association(E1_SCTP_PORT, 45)
        if not obs["e1_setup"]:
            print("[ranbench] warning: no E1 association. The CU-CP has no user-plane node and "
                  "will refuse to admit the UE; the attach is attempted anyway.")

        ran.start("du")
        # The one log wait that is sound. Unlike the CU-CP, the O-DU is chatty enough to push
        # past its buffer, so its file tracks reality while it runs (verified: 12 KB written and
        # the marker present after 20 s). Cell activation also has no socket to observe.
        obs["cell_active"] = ran.wait_for_log("du", "Cell was activated", 60)

    def _attach(self, ue_log: Path, obs: dict) -> None:
        """Launch the UE and wait for the attach milestones to appear in its log."""
        argv = self._argv()
        self.store.record_command(" ".join([*self.sudo, *argv]))
        with open(ue_log, "w") as fh:
            subprocess.Popen([*self.sudo, "setsid", *argv], stdout=fh,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        deadline = time.time() + self.attach_timeout
        while time.time() < deadline:
            if self._log_has(ue_log, "successfully configured"):
                break
            time.sleep(2)
        text = ue_log.read_text(errors="ignore") if ue_log.exists() else ""
        low = text.lower()
        for needle, field in _MILESTONES:
            obs[field] = needle.lower() in low
        m = _UE_IP_RE.search(text)
        obs["ue_ip"] = m.group(1) if m else ""
        m = _TUN_RE.search(text)
        obs["tun"] = m.group(1) if m else ""
        obs["ue_log"] = str(ue_log)
        if obs.get("tun") and obs.get("ue_ip"):
            obs["ping_ok"], obs["ping_detail"] = self._ping(obs["tun"])

    def _ping(self, tun: str) -> tuple[bool, str]:
        try:
            r = self._run("ping", "-I", tun, "-c", "4", "-W", "3", self.ping_target, timeout=25)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
        out = r.stdout + r.stderr
        m = re.search(r"(\d+) received", out)
        return (" 0% packet loss" in out), (m.group(0) if m else out.strip()[-120:])

    # --- evidence -------------------------------------------------------------
    def _decode(self, ran, observer, obs: dict) -> None:
        """Decode every pcap the stack wrote into plain facts, so the suites assert on
        observations rather than re-running tshark per test."""
        obs["pcaps"] = {}
        if observer is None:
            obs["decode_error"] = "no wire observer available (is tshark installed?)"
            return
        archive = Path(self.store.raw) / "pcap"
        for tgt in ("cucp", "cuup", "du"):
            try:
                paths = ran.pcap_paths(tgt)
            except Exception:  # noqa: BLE001
                paths = {}
            obs["pcaps"][tgt] = paths
            for iface, path in paths.items():
                slot = f"{tgt}.{iface}"
                procs = observer.procedures(path)
                obs[f"procs.{slot}"] = sorted(procs) if procs else None
                if iface in ("f1ap", "ngap"):
                    # these two carry the RRC / NAS message names as details, so the suites
                    # need the full info lines, not just the procedure set
                    obs[f"lines.{slot}"] = observer.info_lines(path)
                if iface == "f1ap" and tgt == "cucp":
                    obs["as_security"] = observer.as_security(path)
                    obs["rrc_ciphering"] = observer.rrc_ciphering(path)
                if iface == "e1ap" and tgt == "cucp":
                    obs["security_info"] = observer.security_info(path)
                if iface == "ngap":
                    obs["ngap_security_indication"] = observer.ngap_security_indication(path)
                if iface in ("n3", "f1u"):
                    obs[f"gtpu.{slot}"] = observer.gtpu(path)
                    obs[f"payload_visible.{slot}"] = observer.gtpu_payload_visible(path)
                self._archive(archive, path, slot)

    def _archive(self, archive: Path, path: str, slot: str) -> None:
        """Copy the decoded capture into the campaign so the verdict stays auditable.

        The live files are overwritten the moment any product restarts, which the robustness
        and recovery cases do, later in the same campaign. Without a copy, the evidence behind a
        certificate would be gone by the time the run finished.
        """
        try:
            archive.mkdir(parents=True, exist_ok=True)
            src = Path(path)
            if src.exists() and src.stat().st_size > 0:
                self._run("cp", str(src), str(archive / f"{slot}.pcap"), timeout=30)
                self._run("chmod", "644", str(archive / f"{slot}.pcap"), timeout=10)
        except Exception:  # noqa: BLE001, archiving must never break a measurement
            pass

    def teardown(self) -> None:
        self._kill_ue()
