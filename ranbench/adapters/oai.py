"""OpenAirInterface as the RAN under test: a three-way split gNB (CU-CP / CU-UP / DU).

OAI maps onto the same TS 33.523 product classes ranbench already certifies, so the catalogs
are untouched: ``nr-softmodem`` runs as the CU-CP, ``nr-cuup`` as the CU-UP over E1, and a
second ``nr-softmodem`` as the DU over F1.

Two things differ from OCUDU and shape everything here.

**OAI writes no control-plane captures.** Its only pcap support is MAC-layer OPT, so there is no
NGAP, F1AP or E1AP file to point at. Judging it on its logs is not an option the framework
allows, so ranbench captures the interfaces itself (``observers.capture``) and reports those
files through the same ``pcap_paths`` the OCUDU adapter uses. Nothing above this line can tell
the difference, and the capture is the stronger evidence: a product's own pcap is its account of
what it believes it sent, while a capture off the wire is what actually crossed.

**Two of the three products are the same binary.** The CU-CP and the DU are both
``nr-softmodem``, so a process name cannot tell them apart the way ``ocucp`` and ``odu`` can.
Every process question here is answered by matching the config file the process was started
with, which is the only thing that distinguishes them.

Addresses are read live out of the configs the products actually run with, so re-addressing the
rig needs no change in the campaign file. See docs/OAI-RIG.md for the rig itself.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from ranbench import ports
from ranbench.adapters import _libconfig as lc
from ranbench.adapters.base import RanAdapter
from ranbench.config import expand_user_path
from ranbench.observers.capture import WireCapture

# product class -> (executable, human label)
NODES = {
    "cucp": ("nr-softmodem", "gNB-CU-CP"),
    "cuup": ("nr-cuup", "gNB-CU-UP"),
    "du": ("nr-softmodem", "gNB-DU"),
}

# The DU needs the radio simulator; the others have no radio. Overridable per target.
_DEFAULT_ARGS = {"du": ["--rfsim"]}

_VERSION = re.compile(r"Abrev\.\s*Hash:\s*(\S+)")
_BRANCH = re.compile(r"Branch:\s*(\S+)")


class Adapter(RanAdapter):
    name = "oai"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.ran.extra
        self.bin_dir = expand_user_path(
            e.get("bin_dir", "~/openairinterface5g/cmake_targets/ran_build/build"))
        self.bins: dict[str, Path] = {}
        for tgt, (exe, _) in NODES.items():
            override = (e.get("binaries") or {}).get(tgt)
            self.bins[tgt] = expand_user_path(override) if override else self.bin_dir / exe
        self.configs = {k: expand_user_path(v) for k, v in (e.get("configs") or {}).items()}
        self.args = {**_DEFAULT_ARGS, **(e.get("args") or {})}
        self._conf_cache: dict[str, dict[str, list[str]]] = {}
        self.capture = WireCapture(Path(store.raw) / "capture", store, self.use_sudo)
        self._capture_note: dict[str, str] = {}

    # --- config ---------------------------------------------------------------
    def _conf(self, target: str) -> dict[str, list[str]]:
        if target not in self._conf_cache:
            path = self.configs.get(target)
            self._conf_cache[target] = lc.read(path) if path else {}
        return self._conf_cache[target]

    def _val(self, target: str, key: str, default: str = "") -> str:
        return lc.first(self._conf(target), key, default)

    # --- process plumbing -----------------------------------------------------
    def _pid(self, target: str) -> str:
        """The pid of one product, identified by the config it was started with.

        The CU-CP and the DU are the same executable, so matching on the process name would
        return whichever the kernel listed first and silently answer about the wrong product.
        """
        cfg_path = self.configs.get(target)
        exe = NODES[target][0]
        if not cfg_path:
            return ""
        try:
            r = subprocess.run(["pgrep", "-a", "-f", Path(cfg_path).name],
                               capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return ""
        name = Path(cfg_path).name
        for line in (r.stdout or "").splitlines():
            pid, _, cmd = line.partition(" ")
            # Both must hold: the config names *which* product, the executable confirms it is
            # the product and not something else that merely mentions the file (an editor, a
            # grep, the pgrep itself).
            if name in cmd and exe in cmd and "pgrep" not in cmd:
                return pid.strip()
        return ""

    def node_alive(self, target: str) -> bool | None:
        if target not in NODES:
            return None
        return bool(self._pid(target))

    def _console(self, target: str) -> Path:
        return Path(self.store.raw) / f"oai-{target}-console.log"

    def start(self, target: str) -> bool:
        """Start one product. False if already running or the binary/config is missing."""
        if self.node_alive(target):
            return False
        binary, cfg_path = self.bins.get(target), self.configs.get(target)
        if not binary or not Path(binary).exists() or not cfg_path or not Path(cfg_path).exists():
            return False
        cmd = ((["sudo", "-n"] if self.use_sudo else [])
               + [str(binary), "-O", str(cfg_path)] + list(self.args.get(target, [])))
        self.store.record_command(" ".join(cmd))
        try:
            with open(self._console(target), "w") as fh:
                subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return False
        return True

    def stop(self, target: str, graceful: bool = True) -> None:
        """Stop one product by pid, so the CU-CP and the DU are not confused for each other."""
        pid = self._pid(target)
        if not pid:
            return
        self._run("kill", "-INT" if graceful else "-KILL", pid, timeout=10)

    # --- introspection --------------------------------------------------------
    def _version(self, target: str = "cucp") -> str:
        binary = self.bins.get(target)
        if not binary or not Path(binary).exists():
            return ""
        try:
            r = self._run(str(binary), "--version", timeout=25)
        except (OSError, subprocess.SubprocessError):
            return ""
        text = (r.stdout or "") + (r.stderr or "")
        commit, branch = _VERSION.search(text), _BRANCH.search(text)
        bits = [b.group(1) for b in (branch, commit) if b]
        return "OAI " + " ".join(bits) if bits else "OAI"

    def describe(self) -> dict[str, Any]:
        facts: dict[str, Any] = {"ran": "OpenAirInterface", "adapter": self.name,
                                 "mode": "cucp-cuup-du-split"}
        up, built = [], []
        for tgt in NODES:
            if Path(self.bins[tgt]).exists():
                built.append(tgt)
            if self._pid(tgt):
                up.append(tgt)
        facts["nodes_built"] = ", ".join(built) if built else "(none found)"
        facts["nodes_up"] = ", ".join(up) if up else "(none running)"
        ver = self._version("cucp")
        if ver:
            facts["ran_release"] = ver
        facts["radio"] = "rfsimulator" if "--rfsim" in self.args.get("du", []) else "hardware"
        return facts

    def planned_evidence(self, target: str) -> list[str]:
        """What evidence this target *will* have once the run opens the captures.

        Asked by the doctor, which runs before anything is capturing. For a stack that writes
        its own pcaps this is what it has now; for OAI it is what ranbench will capture, so a
        preflight reports the rig as ready instead of failing on files that cannot exist yet.
        """
        planned = set(self._filters())
        wanted = {"cucp": ("ngap", "f1ap", "e1ap"), "cuup": ("e1ap", "f1u", "n3"),
                  "du": ("f1ap", "f1u")}.get(target, ())
        return [k for k in wanted if k in planned]

    # --- addressing -----------------------------------------------------------
    def amf_address(self) -> str:
        """The AMF the CU-CP dials, from ``amf_ip_address = ({ ipv4 = ... })``."""
        return lc.addr(self._val("cucp", "ipv4"))

    def node_endpoint(self, target: str) -> str:
        """The socket the robustness probes should aim at, per product class.

        Not simply "an address for this product": ``no_crash`` probes exactly this endpoint with
        the protocol the catalog names, so it has to be the socket that actually serves that
        protocol. The CU-CP is judged on its F1-C listener, and the CU-UP and the DU on their
        GTP-U sockets, because the cases against them send malformed GTP-U (TS 29.281).

        Getting this wrong does not fail loudly, it passes: a UDP datagram aimed at an SCTP
        control port is dropped by the kernel, the product is untouched, and "survived" is
        recorded for a probe that never reached it. Both user-plane cases did exactly that
        before this was corrected.
        """
        if target == "cucp":
            a = lc.addr(self._val("cucp", "local_s_address"))
            return f"{a}:{ports.F1C_SCTP_PORT}" if a else ""
        if target == "cuup":
            # the CU-UP binds F1-U on local_s_address; NG-U is the same process
            a = lc.addr(self._val("cuup", "local_s_address"))
            port = self._val("cuup", "local_s_portd", str(ports.GTPU_UDP_PORT))
            return f"{a}:{port}" if a else ""
        if target == "du":
            a = lc.addr(self._val("du", "local_n_address"))
            port = self._val("du", "local_n_portd", str(ports.GTPU_UDP_PORT))
            return f"{a}:{port}" if a else ""
        return ""

    def _f1u_addrs(self) -> tuple[str, str]:
        """(CU-UP side, DU side) of F1-U. The CU-UP binds F1-U on ``local_s_address``."""
        return (lc.addr(self._val("cuup", "local_s_address")),
                lc.addr(self._val("du", "local_n_address")))

    def interface_addrs(self, iface: str) -> list[str]:
        key = iface.upper()
        out: list[str] = []
        if "N2" in key:
            out += [lc.addr(self._val("cucp", "GNB_IPV4_ADDRESS_FOR_NG_AMF")), self.amf_address()]
        if "F1-C" in key:
            out += [lc.addr(self._val("cucp", "local_s_address")),
                    lc.addr(self._val("du", "local_n_address"))]
        if "E1" in key:
            out += [lc.addr(self._val("cuup", "ipv4_cucp")), lc.addr(self._val("cuup", "ipv4_cuup"))]
        if "F1-U" in key:
            out += list(self._f1u_addrs())
        if "N3" in key:
            out += [lc.addr(self._val("cuup", "GNB_IPV4_ADDRESS_FOR_NGU"))]
        return [a for a in out if a]

    # --- evidence -------------------------------------------------------------
    def _filters(self) -> dict[str, str]:
        """One capture per interface, keyed the way the suites name their evidence.

        F1-U and N3 are both GTP-U on the same port and are separated only by address, which is
        why each pins its hosts rather than the port alone.
        """
        cuup_f1u, du_f1u = self._f1u_addrs()
        gtp = self._val("cuup", "local_s_portd", str(ports.GTPU_UDP_PORT))
        ngu = lc.addr(self._val("cuup", "GNB_IPV4_ADDRESS_FOR_NGU"))
        f: dict[str, str] = {
            "ngap": f"sctp port {ports.NGAP_SCTP_PORT}",
            "f1ap": f"sctp port {ports.F1C_SCTP_PORT}",
            "e1ap": f"sctp port {ports.E1_SCTP_PORT}",
        }
        if cuup_f1u and du_f1u:
            f["f1u"] = f"udp port {gtp} and host {cuup_f1u} and host {du_f1u}"
        if ngu:
            f["n3"] = f"udp port {ports.GTPU_UDP_PORT} and host {ngu}"
        return f

    def begin_evidence(self) -> None:
        self.capture.start(self._filters())

    def end_evidence(self) -> None:
        self.capture.stop()
        # A capture that ran and caught nothing is our filter being wrong, not the product being
        # silent. Recorded so it can be reported as such rather than passing for absent evidence.
        self._capture_note = self.capture.empty()

    def pcap_paths(self, target: str) -> dict[str, str]:
        """The captures relevant to one product class.

        F1-C is one wire, so the CU-CP's and the DU's F1AP evidence is the same file read twice,
        not two independent observations. Same for E1 between the CU-CP and the CU-UP.
        """
        got = self.capture.paths()
        wanted = {
            "cucp": ("ngap", "f1ap", "e1ap"),
            "cuup": ("e1ap", "f1u", "n3"),
            "du": ("f1ap", "f1u"),
        }.get(target, ())
        return {k: got[k] for k in wanted if k in got}

    def empty_captures(self) -> dict[str, str]:
        """Captures that ran but caught nothing, as ``{interface: filter}``."""
        return dict(self._capture_note)

    def log_paths(self, target: str) -> dict[str, str]:
        """OAI logs to stdout, so the console file ``start`` redirected into is the log."""
        p = self._console(target)
        return {"console": str(p)} if p.exists() else {}

    def wait_for_cell(self, timeout: float = 60.0) -> bool:
        """OAI announces the cell at the CU-CP, once the DU's F1 Setup has been accepted:

            NR_RRC  cell PLMN 208.93 Cell ID 12345678 is in service

        Taken from the CU-CP rather than the DU because that line is the statement that the cell
        was admitted and is serving, not merely that the DU brought its radio up. Unlike OCUDU,
        OAI's console is not block-buffered, so it is readable while the process runs.
        """
        return self.wait_for_log("cucp", "is in service", timeout)

    def node_log_grep(self, target: str, patterns: list[str], tail: int = 800) -> bool | None:
        p = self._console(target)
        if not p.exists():
            return None
        try:
            lines = p.read_text(errors="ignore").splitlines()[-tail:]
        except OSError:
            return None
        hay = "\n".join(lines).lower()
        return any(pat.lower() in hay for pat in patterns)

    def wait_for_log(self, target: str, needle: str, timeout: float = 40.0) -> bool:
        """OAI writes its console unbuffered enough to follow, unlike OCUDU. Still never a
        verdict: this only orders the bring-up."""
        import time
        deadline = time.time() + timeout
        p = self._console(target)
        while time.time() < deadline:
            try:
                if p.exists() and needle.lower() in p.read_text(errors="ignore").lower():
                    return True
            except OSError:
                pass
            time.sleep(0.5)
        return False
