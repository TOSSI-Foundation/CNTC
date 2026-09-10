"""An L1/L2 split gNB as the RAN under test: PNF and VNF across nFAPI (SCF split 6).

This is a different cut from the CU/DU adapters. There the three products are all 3GPP TS 33.523
classes on one gNB; here the certified pair are the two ends of the SCF nFAPI interface, and the
deployment carries two further processes that are peers rather than subjects:

    UE --rfsim--> PNF (L1) <--nFAPI P5/P7--> VNF <--FAPI--> L2 (MAC/RLC) <--F1--> CU (RRC/PDCP)
                  ^^^ target                 ^^^ target      peer                 peer

**Only the PNF and the VNF are certified.** The L2 and the CU are named here because the stack
does not assemble without them, and because a run has to start and stop them, not because
anything grades them. Their catalogs, if wanted, are the existing CU/DU ones.

Four things differ from every adapter before this one, and each of them breaks an assumption
the engine previously got away with.

**The start order is load-bearing and asymmetric.** CU, then VNF, then PNF, then L2. The VNF is
the DPDK primary and must create the shared memory region before the L2 attaches to it; the PNF
must have completed its P5 handshake before the L2 starts, because the L2 sends PARAM.request
exactly once and the VNF discards it if the PNF is not yet RUNNING. There is no retry anywhere
in that chain.

**Restarting the PNF means restarting the VNF.** The VNF does not repeat the P5 handshake, so a
PNF that comes back finds a VNF that will never configure it again. Anything that restarts one
of them therefore restarts the southbound chain as a unit, which is why ``start`` does more than
its name suggests. Getting this wrong does not fail loudly: the products are all running and
nothing crosses P7.

**State outlives the processes.** The VNF is a DPDK primary with a hugepage file prefix, and a
run that ends abruptly leaves the region behind. The next run then attaches to a stale region
instead of creating a fresh one, so the prefix is removed before anything starts.

**The interface is UDP and SCTP, not SCTP alone.** P7 is UDP, so the SCTP association checks the
CU/DU adapters rely on say nothing here. Readiness is observed on the socket that actually
carries the interface.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ranbench import ports
from ranbench.adapters.base import RanAdapter
from ranbench.config import expand_user_path
from ranbench.observers.capture import WireCapture

# The two certified product classes, and the two peers the deployment needs to assemble.
TARGETS = ("pnf", "vnf")
PEERS = ("cu", "l2")

# Bring-up order. Not a preference: see the module docstring.
ORDER = ("cu", "vnf", "pnf", "l2")


class Adapter(RanAdapter):
    name = "fapi_split"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.ran.extra
        self.procs: dict[str, dict] = {}
        for key, spec in (e.get("procs") or {}).items():
            spec = dict(spec)
            spec["bin"] = expand_user_path(spec.get("bin", ""))
            if spec.get("cwd"):
                spec["cwd"] = expand_user_path(spec["cwd"])
            spec["args"] = [str(expand_user_path(a)) if str(a).startswith("~") else str(a)
                            for a in (spec.get("args") or [])]
            self.procs[key] = spec
        p = e.get("nfapi_ports") or {}
        self.p5_pnf = int(p.get("p5_pnf", ports.NFAPI_P5_PNF_PORT))
        self.p5_vnf = int(p.get("p5_vnf", ports.NFAPI_P5_VNF_PORT))
        self.p7_pnf = int(p.get("p7_pnf", ports.NFAPI_P7_PNF_PORT))
        self.p7_vnf = int(p.get("p7_vnf", ports.NFAPI_P7_VNF_PORT))
        self.host = e.get("nfapi_host", "127.0.0.1")
        # The CU's F1-C listener, which the L2 dials exactly once.
        self.f1c_port = int(e.get("f1c_port", ports.F1C_SCTP_PORT))
        # The radio simulator's server port, which the PNF binds and the UE dials.
        self.rfsim_port = int(e.get("rfsim_port", 4043))
        self._rig_faults: list[str] = []
        # DPDK state the VNF owns, removed before every run. See the docstring.
        self.dpdk_prefix = e.get("dpdk_file_prefix", "gnb0")
        self.capture = WireCapture(Path(store.raw) / "capture", store, self.use_sudo)
        self._capture_note: dict[str, str] = {}
        self._settle = float(e.get("settle_s", 12))

    # --- process plumbing -----------------------------------------------------
    def _procname(self, key: str) -> str:
        """The executable to look for. A launcher script execs something else, so a config may
        name the process separately from the command used to start it."""
        spec = self.procs.get(key) or {}
        return spec.get("proc") or Path(spec.get("bin", "")).name

    def _pid(self, key: str) -> str:
        name = self._procname(key)
        if not name:
            return ""
        try:
            r = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return ""
        return (r.stdout or "").split("\n")[0].strip()

    def _alive(self, key: str) -> bool:
        return bool(self._pid(key))

    def _console(self, key: str) -> Path:
        return Path(self.store.raw) / f"fapi-{key}-console.log"

    def _launch(self, key: str) -> bool:
        spec = self.procs.get(key)
        if not spec or not spec.get("bin") or not Path(spec["bin"]).exists():
            return False
        cmd: list[str] = ["sudo", "-n"] if self.use_sudo else []
        cmd += ["setsid"]
        # Pinning is not tuning here. The host isolates cores for the real-time threads, and an
        # unpinned L1 or L2 lands on the housekeeping cores alongside the OS and the UE, where
        # it misses slot deadlines and the run measures the scheduler rather than the product.
        if spec.get("cpus"):
            cmd += ["taskset", "-c", str(spec["cpus"])]
        cmd += [str(spec["bin"]), *spec.get("args", [])]
        self.store.record_command(" ".join(cmd))
        try:
            with open(self._console(key), "w") as fh:
                subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True,
                                 cwd=str(spec["cwd"]) if spec.get("cwd") else None)
        except OSError:
            return False
        return True

    def _kill(self, key: str, graceful: bool = True) -> None:
        name = self._procname(key)
        if not name:
            return
        # -x, never -f: a pattern match would also match the sudo parent that is running it,
        # and killing that takes the campaign down with the product.
        self._run("pkill", "-INT" if graceful else "-KILL", "-x", name, timeout=10)

    def _await_gone(self, key: str, timeout: float = 15) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline and self._alive(key):
            time.sleep(0.5)

    # --- readiness, on the socket that carries the interface ------------------
    def _udp_bound(self, port: int) -> bool:
        try:
            r = self._run("ss", "-anu", timeout=10)
        except (OSError, subprocess.SubprocessError):
            return False
        return any(f":{port}" in ln for ln in (r.stdout or "").splitlines())

    def wait_for_udp(self, port: int, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._udp_bound(port):
                return True
            time.sleep(1)
        return False

    def rfsim_conflict(self) -> str:
        """Whoever already holds the radio simulator's port, or empty if it is free.

        Worth a check of its own because the failure is silent and total. The PNF logs
        "Could not start the RF device" and then transmits void samples: every product stays
        up, P5 completes, P7 runs at full rate, and the only symptom is a UE that connects to
        the simulator and never sees a signal. Measured on this rig, an unrelated gNB left
        running by other work on the same host held the port for hours, and two campaigns
        recorded the PHY as faulty for it.
        """
        try:
            r = self._run("ss", "-anlpt", timeout=10)
        except (OSError, subprocess.SubprocessError):
            return ""
        for line in (r.stdout or "").splitlines():
            if f":{self.rfsim_port}" in line and "LISTEN" in line:
                m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
                return f"{m.group(1)} (pid {m.group(2)})" if m else "an unidentified process"
        return ""

    def rig_faults(self) -> list[str]:
        """Conditions that make the rig unable to measure anything, collected during bring-up.

        These are not verdicts and never become one. They exist so a run says "this rig could
        not produce a stimulus" instead of quietly recording the resulting silence against the
        product.
        """
        return list(self._rig_faults)

    def wait_for_cu_f1c(self, timeout: float = 60.0) -> bool:
        """Block until the CU is accepting on F1-C.

        Waited for rather than slept through, because the L2 makes exactly one F1-C connection
        attempt and exits when it is refused. Measured on this rig: the CU completed NG Setup
        and had still not bound its F1-C listener, the L2 was refused, and the run went on to
        measure a PHY that was transmitting nothing because no cell had been admitted. Every
        requirement past that point recorded 'na' or, worse, blamed the PNF.
        """
        return self.wait_for_listen(self.f1c_port, timeout)

    def wait_for_p5(self, timeout: float = 45.0) -> bool:
        """Block until the PNF has an established P5 association with the VNF.

        P5 is SCTP (SCF225), so this is the one place the inherited association check applies,
        and it is the only honest signal that the two ends found each other: the VNF logs that
        it is listening long before anything connects.
        """
        return self.wait_for_association(self.p5_vnf, timeout)

    # --- lifecycle ------------------------------------------------------------
    def deploy(self) -> None:
        """Clear state that outlives a process, so a run starts from a defined rig."""
        self.reset()

    def reset(self) -> None:
        for key in reversed(ORDER):
            self._kill(key, graceful=False)
        for key in reversed(ORDER):
            self._await_gone(key, 10)
        self._clear_dpdk()

    def _clear_dpdk(self) -> None:
        """Remove the VNF's hugepage region and runtime directory.

        A run that ends abruptly leaves both behind, and the next VNF then attaches to the stale
        region rather than creating a fresh one. Nothing reports that: the products start, and
        the L2 simply never sees a slot.
        """
        for path in (f"/dev/hugepages/{self.dpdk_prefix}*",
                     f"/var/run/dpdk/{self.dpdk_prefix}"):
            self._run("sh", "-c", f"rm -rf {path}", timeout=15)

    def teardown(self) -> None:
        self.reset()

    def start(self, target: str) -> bool:
        """Start a product, bringing up whatever it cannot exist without.

        ``start`` doing more than its name says is deliberate and is forced by the stack. The
        VNF does not repeat the P5 handshake, so a PNF restarted on its own comes back to a VNF
        that will never configure it; and the L2 asks for its PHY parameters exactly once, so it
        has to follow a PNF that is already attached. Restarting either certified product
        therefore rebuilds the southbound chain in order. The CU is left alone, because it holds
        the N2 association and restarting it would cost the core's view of the whole gNB.
        """
        if target in PEERS:
            return self._start_one(target)
        if target not in TARGETS:
            return False
        return self._start_chain()

    def _start_one(self, key: str) -> bool:
        if self._alive(key):
            return False
        return self._launch(key)

    def _start_chain(self) -> bool:
        """VNF, then PNF, then L2, each waited for. The CU is assumed already up."""
        for key in ("l2", "pnf", "vnf"):
            self._kill(key, graceful=False)
        for key in ("l2", "pnf", "vnf"):
            self._await_gone(key, 10)
        self._clear_dpdk()

        if not self._launch("vnf"):
            return False
        if not self.wait_for_udp(self.p7_vnf, 30):
            return False                      # the VNF never bound P7; nothing can attach
        # Checked before the PNF starts, because afterwards the symptom is indistinguishable
        # from a PHY that simply is not transmitting.
        holder = self.rfsim_conflict()
        if holder:
            self._rig_faults.append(
                f"the radio simulator port {self.rfsim_port} was already held by {holder}, so "
                f"this PNF cannot bind it and will transmit void samples. No UE can synchronise "
                f"and nothing measured against it describes the PHY.")
        if not self._launch("pnf"):
            return False
        time.sleep(6)
        # The product's own account, used to detect a broken rig and never to grade anything.
        console = self._console("pnf")
        try:
            if console.exists() and "Could not start the RF device" in console.read_text(
                    errors="ignore"):
                self._rig_faults.append(
                    "the PNF could not start its radio device and is generating void samples, "
                    "so it is transmitting nothing for a UE to find.")
        except OSError:
            pass
        if not self.wait_for_p5(45):
            return False                      # no P5 association, so the L2 would be discarded
        if not self._launch("l2"):
            return False
        # The L2 drives the PHY-instance half of P5 and only then does P7 begin.
        self.wait_for_udp(self.p7_pnf, 30)
        time.sleep(self._settle)
        return self._alive("pnf") and self._alive("vnf")

    def stop(self, target: str, graceful: bool = True) -> None:
        """Stop one product. Stopping either certified product stops what depends on it.

        The L2 is taken down with them, because a L2 left running against a PNF that has gone
        away spins on a slot indication that will never arrive and holds the shared memory
        region open, which blocks the next VNF from creating it.
        """
        if target in PEERS:
            self._kill(target, graceful)
            return
        if target not in TARGETS:
            return
        self._kill("l2", graceful)
        self._await_gone("l2", 8)
        self._kill(target, graceful)

    # --- introspection --------------------------------------------------------
    _VERSION_PATTERNS = (
        r"commit\s+([0-9a-f]{7,40})",        # OCUDU prints "OCUDU CU (commit 7f99fd2527)"
        r"Abrev\.\s*Hash:\s*(\S+)",         # OAI prints a build banner
    )

    def _version(self, key: str) -> str:
        """The component's own identity, asked of the binary and never guessed.

        A certificate names what was tested, so the version has to come from the thing that ran
        rather than from a source tree that may have moved on since it was built. Components
        that cannot report one carry a ``release`` string from the campaign instead, and one
        that offers neither is reported as unknown rather than filled in.
        """
        spec = self.procs.get(key) or {}
        if spec.get("release"):
            return str(spec["release"])
        binary = spec.get("bin")
        if not binary or not Path(binary).exists():
            return ""
        try:
            r = self._run(str(binary), "--version", timeout=20)
        except (OSError, subprocess.SubprocessError):
            return ""
        text = (r.stdout or "") + (r.stderr or "")
        import re
        for pattern in self._VERSION_PATTERNS:
            m = re.search(pattern, text)
            if m:
                return m.group(1)
        return ""

    def describe(self) -> dict[str, Any]:
        facts: dict[str, Any] = {"ran": "L1/L2 split over nFAPI", "adapter": self.name,
                                 "mode": "fapi-split (SCF split 6)"}
        built = [k for k in ORDER if self.procs.get(k) and Path(self.procs[k]["bin"]).exists()]
        up = [k for k in ORDER if self._alive(k)]
        facts["nodes_built"] = ", ".join(built) if built else "(none found)"
        facts["nodes_up"] = ", ".join(up) if up else "(none running)"
        facts["nfapi"] = (f"P5 sctp {self.p5_pnf}<->{self.p5_vnf}, "
                          f"P7 udp {self.p7_pnf}<->{self.p7_vnf}")
        facts["pnf"] = self._procname("pnf")
        facts["vnf"] = self._procname("vnf")
        # Four independently built components, so there is no single release to name. Each is
        # reported separately: a mixed-vendor result is only reproducible if every side is.
        versions = {k: self._version(k) for k in ORDER}
        named = [f"{k}={v}" for k, v in versions.items() if v]
        if named:
            facts["ran_release"] = ", ".join(named)
        return facts

    def node_alive(self, target: str) -> bool | None:
        if target not in TARGETS + PEERS:
            return None
        if not self.procs.get(target):
            return None
        return self._alive(target)

    def node_endpoint(self, target: str) -> str:
        """The socket the robustness case should probe, per product class.

        Both are probed on their own P7 socket. P7 is the interface that carries the slot loop
        and it is UDP, so a malformed datagram actually reaches the product; the PNF exposes no
        P5 listener at all (it dials the VNF), so aiming at P5 would probe nothing on that side
        and the product would record "survived" for a datagram it never received.
        """
        if target == "pnf":
            return f"{self.host}:{self.p7_pnf}"
        if target == "vnf":
            return f"{self.host}:{self.p7_vnf}"
        return ""

    def interface_addrs(self, iface: str) -> list[str]:
        key = iface.upper()
        if "NFAPI" in key or "P5" in key or "P7" in key:
            return [self.host]
        return []

    def wait_for_cell(self, timeout: float = 60.0) -> bool:
        """The cell is the L2's to announce, and it only serves once P7 is running.

        Judged on the PNF's P7 socket rather than a log line: the products here write their logs
        at different verbosities and one of them buffers, so the socket is the only account that
        does not depend on what a release chose to print.
        """
        return self.wait_for_udp(self.p7_pnf, timeout)

    # --- evidence -------------------------------------------------------------
    def _filters(self) -> dict[str, str]:
        """One capture per interface, keyed the way the suites name their evidence.

        P5 is matched without naming a transport. SCF225 specifies SCTP and both implementations
        here use it, but that is a requirement the catalog *tests* (PNF-P5-09), so the filter
        must be able to see a deployment that got it wrong. A bare ``port`` matches TCP, UDP and
        SCTP alike, and the observer reports which one actually carried it.
        """
        return {
            "p5": f"port {self.p5_pnf} or port {self.p5_vnf}",
            "p7": f"udp port {self.p7_pnf} or udp port {self.p7_vnf}",
        }

    def begin_evidence(self) -> None:
        self.capture.start(self._filters())

    def end_evidence(self) -> None:
        self.capture.stop()
        self._capture_note = self.capture.empty()

    def pcap_paths(self, target: str) -> dict[str, str]:
        """The same two captures serve both product classes.

        This is not a shortcut. P5 and P7 are single wires with a PNF at one end and a VNF at
        the other, so one capture holds both products' behaviour and the observer separates them
        by direction. Capturing twice would produce two copies of one observation.
        """
        got = self.capture.paths()
        return {k: got[k] for k in ("p5", "p7") if k in got}

    def planned_evidence(self, target: str) -> list[str]:
        return [k for k in ("p5", "p7") if k in self._filters()]

    def empty_captures(self) -> dict[str, str]:
        return dict(self._capture_note)

    def log_paths(self, target: str) -> dict[str, str]:
        """Console logs for the product and, for diagnosis only, its peers.

        A PNF that never reached RUNNING usually says so in the L2's log rather than its own,
        because the L2 is what asked it to start. These are never a verdict.
        """
        out: dict[str, str] = {}
        for key in ORDER:
            p = self._console(key)
            if p.exists():
                out[key] = str(p)
        return out

    def available(self) -> bool:
        return bool(shutil.which("pgrep"))
