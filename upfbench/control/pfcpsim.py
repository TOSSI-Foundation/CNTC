"""Portable PFCP/N4 control by wrapping omec-project/pfcpsim (vendored in third_party/).

pfcpsim runs a gRPC *server* that holds the PFCP/SMF state; the `pfcpctl` client drives
it. PFCP messages are encoded per 3GPP TS 29.244 by pfcpsim, so conformance (Suite 3) and
multi-UE session install (Suite 2) ride on a spec-correct stack instead of hand-rolled
PFCP. This control owns the pfcpsim server lifecycle (start in setup, stop in teardown)
and exposes each N4 procedure as a method returning a structured ``{ok, output}`` result
so the CF-* conformance tests can assert on it.

Verified surface against the SD-Core BESS-UPF (this pfcpsim build *does* support
``session modify``, so CF-03 is testable):
    pfcpctl -s <srv> service configure --remote-peer-addr <UPF-N4> --n3-addr <N3>
    pfcpctl -s <srv> service associate | disassociate
    pfcpctl -s <srv> session create|modify|delete --count N --baseID B [--ue-pool ..] [--gnb-addr ..]

Config knobs (campaign YAML ``upf.extra``, defaults shown):
    pfcpsim_dir:        third_party/pfcpsim   # where the built pfcpsim/pfcpctl live
    pfcpsim_port:       54321                 # pfcpsim gRPC server port
    pfcpsim_iface:      eth0                  # local interface pfcpsim sends PFCP from
    pfcp_remote_addr:   ""                    # UPF N4 addr; "" -> resolve `upf` svc ClusterIP
    pfcp_service:       upf                   # k8s service to resolve when remote is blank
    n3_addr:            192.168.252.3         # UPF N3 IP advertised in F-TEIDs
    gnb_addr:           192.168.252.10        # gNB N3 addr for downlink tunnels
    ue_pool:            10.250.0.0/24         # UE address pool for created sessions
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from upfbench.control.base import ControlPlane

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Control(ControlPlane):
    name = "pfcpsim"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.extra
        d = e.get("pfcpsim_dir", "third_party/pfcpsim")
        base = Path(d) if Path(d).is_absolute() else _REPO_ROOT / d
        self.pfcpsim_bin = str(base / "pfcpsim")
        self.pfcpctl_bin = str(base / "pfcpctl")
        self.port = str(e.get("pfcpsim_port", 54321))
        self.server = f"localhost:{self.port}"
        self.iface = e.get("pfcpsim_iface", "eth0")
        self.n3_addr = e.get("n3_addr", "192.168.252.3")
        self.gnb_addr = e.get("gnb_addr", "192.168.252.10")
        self.ue_pool = e.get("ue_pool", "10.250.0.0/24")
        # remote UPF N4 address (the PFCP agent)
        self.remote = e.get("pfcp_remote_addr", "") or self._resolve_remote(
            e.get("pfcp_service", "upf"))
        # kube knobs (to resolve the service IP + recover the pfcp-agent)
        self.namespace = e.get("namespace", "aether-5gc")
        self.kubectl = e.get("kubectl", "kubectl")
        self.kubeconfig = e.get("kubeconfig", "")
        self.pod = e.get("pod", "upf-0")
        self.pfcp_container = e.get("pfcp_agent_container", "pfcp-agent")
        # Omit URRs for UPFs whose PFCP parser rejects them (e.g. OAI-UPF). The
        # OMEC/SD-Core BESS-UPF accepts URRs, so this defaults off.
        self.no_urr = bool(e.get("pfcpsim_no_urr", False))
        # Some UPFs (e.g. OAI-UPF) never answer a PFCP Association Release Request
        # (TS 29.244 §7.4.5) — they tear associations down via heartbeat loss only.
        # When declared, we skip the release (it would just time out) and the
        # conformance suite reports it as a documented capability gap, not a failure.
        self.release_supported = not bool(e.get("pfcp_no_assoc_release", False))
        # Emit the 2-octet Apply Action IE (Rel-16). OMEC/SD-Core + OAI accept the
        # default 1-octet form; Open5GS's parser rejects it ("Invalid TLV length 1.
        # It should be 2"), so set this for Open5GS.
        self.apply_action_2b = bool(e.get("pfcpsim_apply_action_2b", False))
        # DNN to put in each PDR's Network Instance IE. Open5GS allocates the UE IP
        # per-DNN and rejects sessions whose PDRs carry no Network Instance; set this
        # to the UPF's DNN (e.g. "internet"). Empty -> omit (OMEC/OAI don't need it).
        self.dnn = e.get("pfcpsim_dnn", "")
        # Override every QER MBR (kbps) installed per session. Set very high to make
        # the QER effectively unlimited so a QER-enforcing UPF (BESS) is measured at
        # its raw datapath ceiling — the same way a UPF that ignores QER (OAI) is.
        # Unset -> pfcpsim's defaults (rate-limited subscriber model).
        self.mbr_kbps = e.get("pfcpsim_mbr_kbps")
        self._server: subprocess.Popen | None = None
        self.associated = False

    # --- service IP resolution ------------------------------------------------
    def _resolve_remote(self, service: str) -> str:
        cmd = [self.cfg.extra.get("kubectl", "kubectl")]
        if self.cfg.extra.get("kubeconfig"):
            cmd += ["--kubeconfig", self.cfg.extra["kubeconfig"]]
        cmd += ["get", "svc", service, "-n", self.cfg.extra.get("namespace", "aether-5gc"),
                "-o", "jsonpath={.spec.clusterIP}"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        ip = proc.stdout.strip()
        if proc.returncode != 0 or not ip:
            # fall back to config n4_addr (strip any :port)
            return (self.cfg.n4_addr or "").split(":")[0]
        return ip

    # --- pfcpctl plumbing -----------------------------------------------------
    def _ctl(self, *args: str) -> dict[str, Any]:
        cmd = [self.pfcpctl_bin, "-s", self.server, *args]
        self.store.record_command(" ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = (proc.stdout + proc.stderr).strip()
        return {"ok": proc.returncode == 0, "rc": proc.returncode, "output": out}

    # --- server lifecycle -----------------------------------------------------
    def _restart_server(self) -> None:
        """Stop and relaunch pfcpsim. pfcpsim cannot re-associate on a connection it
        previously disassociated (the re-Association response times out client-side even
        though the UPF accepts it), so a clean re-association needs a fresh server."""
        if self._server and self._server.poll() is None:
            self._server.terminate()
            try:
                self._server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._server.kill()
        self._server = None
        self.associated = False
        self._start_server()

    def _start_server(self) -> None:
        if self._server and self._server.poll() is None:
            return
        # free the port in case a stale pfcpsim server is still bound to it
        subprocess.run(["fuser", "-k", f"{self.port}/tcp"], capture_output=True)
        time.sleep(0.5)
        log = open(self.store.raw / "pfcpsim-server.log", "w")
        env = dict(os.environ)
        if self.no_urr:
            env["PFCPSIM_NO_URR"] = "1"
        if self.mbr_kbps:
            env["PFCPSIM_MBR_KBPS"] = str(self.mbr_kbps)
        if self.apply_action_2b:
            env["PFCPSIM_APPLY_ACTION_2B"] = "1"
        if self.dnn:
            env["PFCPSIM_DNN"] = str(self.dnn)
        prefix = (f"{'PFCPSIM_NO_URR=1 ' if self.no_urr else ''}"
                  f"{f'PFCPSIM_MBR_KBPS={self.mbr_kbps} ' if self.mbr_kbps else ''}")
        self.store.record_command(
            f"{prefix}{self.pfcpsim_bin} -p {self.port} -i {self.iface}")
        self._server = subprocess.Popen([self.pfcpsim_bin, "-p", self.port, "-i", self.iface],
                                        stdout=log, stderr=subprocess.STDOUT, env=env)
        # wait until the gRPC port answers (a harmless `service configure` would also do)
        for _ in range(20):
            time.sleep(0.25)
            if self._server.poll() is not None:
                raise RuntimeError("pfcpsim server exited at startup; see raw/pfcpsim-server.log")
            probe = subprocess.run([self.pfcpctl_bin, "-s", self.server, "service", "configure",
                                    "--remote-peer-addr", self.remote, "--n3-addr", self.n3_addr],
                                   capture_output=True, text=True)
            if probe.returncode == 0:
                return
        raise RuntimeError("pfcpsim server did not become ready")

    # --- N4 procedures (each returns {ok, output}) ----------------------------
    def configure(self) -> dict[str, Any]:
        return self._ctl("service", "configure", "--remote-peer-addr", self.remote,
                         "--n3-addr", self.n3_addr)

    def associate(self) -> dict[str, Any]:
        r = self._ctl("service", "associate")
        if not r["ok"] and self._is_datapath_flake(r["output"]):
            # Known SD-Core BESS-UPF flakiness: under repeated PFCP churn the
            # pfcp-agent<->bessd datapath goes "down" and rejects associations.
            # Restarting pfcpiface reconnects it; then re-associate from a fresh server.
            self._restart_pfcp_agent()
            self._restart_server()
            self.configure()
            r = self._ctl("service", "associate")
        self.associated = self.associated or r["ok"]
        return r

    @staticmethod
    def _is_datapath_flake(output: str) -> bool:
        o = output.lower()
        return ("datapath down" in o or "association is not active" in o
                or "association failed" in o or "invalid response" in o)

    def _restart_pfcp_agent(self) -> None:
        """Restart pfcpiface in the UPF pod to clear a stale bessd datapath link."""
        cmd = [self.kubectl]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        cmd += ["exec", "-n", self.namespace, self.pod, "-c", self.pfcp_container,
                "--", "pkill", "-f", "pfcpiface"]
        self.store.record_command(" ".join(cmd) + "  # recover: clear datapath-down")
        subprocess.run(cmd, capture_output=True, text=True)
        time.sleep(7)   # let pfcpiface come back and reconnect to bessd

    def disassociate(self) -> dict[str, Any]:
        if not self.release_supported:
            # Release is a no-op for this UPF (no Association Release Response), so nothing
            # is actually torn down — the association is still live. Keep it: forcing a
            # re-association (fresh server, new Recovery Time Stamp) would needlessly churn
            # UPFs that run PFCP restoration on a peer recovery-timestamp change (Open5GS),
            # which flakes the first establishment of the next test.
            return {"ok": False, "rc": 0, "unsupported": True,
                    "output": "Association Release not supported by this UPF; kept association"}
        r = self._ctl("service", "disassociate")
        # Mark the channel down regardless of the result: after a release *attempt* the
        # association is unreliable (some UPFs, e.g. OAI, don't ack release), so the next
        # session test must force a clean re-association rather than reuse a dead channel.
        self.associated = False
        return r

    def ensure_associated(self) -> None:
        """Guarantee an active association for session tests. If not currently
        associated (e.g. CF-01 released it), restart the server for a clean re-association
        and configure again before associating."""
        if self.associated:
            return
        self._restart_server()
        self.configure()
        self.associate()
        time.sleep(1.0)   # let the fresh association settle before sessions (OAI is strict)

    def aligned_flows(self, count: int = 1, base_id: int = 1):
        """pfcpsim installs a specific F-TEID + UE-IP PDR per session, so N3 traffic
        must match those exact (TEID, UE-IP) pairs."""
        from upfbench.flows import session_flows
        return session_flows(base_id, count, self.ue_pool)

    def create_sessions(self, count: int = 1, base_id: int = 1, **kw) -> dict[str, Any]:
        """Create ``count`` sessions. The first establishment right after a fresh
        (re)association can flake on strict UPFs (e.g. OAI under back-to-back suite
        churn), so retry up to twice with a clean re-association before reporting
        failure. A genuine capacity/limit rejection still fails on every attempt, so
        this never masks a real ceiling — it only absorbs the first-attempt flake."""
        def _do() -> dict[str, Any]:
            return self._ctl("session", "create", "--count", str(count), "--baseID", str(base_id),
                             "--ue-pool", kw.get("ue_pool", self.ue_pool),
                             "--gnb-addr", kw.get("gnb_addr", self.gnb_addr))
        r = _do()
        for _ in range(2):
            if r["ok"]:
                break
            self.associated = False     # force a clean re-association, then retry
            self.ensure_associated()
            r = _do()
        return r

    def modify_sessions(self, count: int = 1, base_id: int = 1, **kw) -> dict[str, Any]:
        return self._ctl("session", "modify", "--count", str(count), "--baseID", str(base_id),
                         "--ue-pool", kw.get("ue_pool", self.ue_pool),
                         "--gnb-addr", kw.get("gnb_addr", self.gnb_addr))

    def delete_sessions_raw(self, count: int = 1, base_id: int = 1) -> dict[str, Any]:
        return self._ctl("session", "delete", "--count", str(count), "--baseID", str(base_id))

    # --- ControlPlane contract (used by the runner + Suite 2) -----------------
    def setup(self) -> None:
        self._start_server()
        self.configure()

    def install_sessions(self, count: int = 1, base_id: int = 1, **kw) -> dict[str, Any]:
        self.ensure_associated()
        r = self.create_sessions(count, base_id, **kw)
        return {"count": count, "base_id": base_id, **r}

    def modify_session(self, session_id: int = 1, count: int = 1, **kw) -> dict[str, Any]:
        return self.modify_sessions(count, session_id, **kw)

    def delete_sessions(self, count: int = 1, base_id: int = 1) -> dict[str, Any]:
        r = self.delete_sessions_raw(count, base_id)
        return {"count": count, "base_id": base_id, **r}

    def teardown(self) -> None:
        try:
            if self.associated:
                self.disassociate()
        finally:
            if self._server and self._server.poll() is None:
                self._server.terminate()
                try:
                    self._server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._server.kill()
