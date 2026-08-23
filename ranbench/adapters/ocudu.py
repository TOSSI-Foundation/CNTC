"""OCUDU adapter, the Linux Foundation CU/DU stack (srsRAN lineage), run as host processes.

OCUDU builds five applications; the three that matter here are the split-gNB product classes::

    ocucp   O-CU-CP   NGAP (N2) + F1AP listener (SCTP 38472) + E1AP listener (SCTP 38462) + RRC
    ocuup   O-CU-UP   E1AP client + F1-U (GTP-U) + N3/NG-U (GTP-U)
    odu     O-DU      F1AP client + F1-U (GTP-U) + RLC/MAC/PHY + the cell

Everything this adapter reports is resolved **live**:
  * liveness  -> is the process running (``pgrep``)
  * version   -> ``<binary> --version`` (prints the build commit)
  * endpoints -> parsed out of the YAML configuration each process was started with
  * pcaps     -> the ``pcap:`` section of that same YAML

Reading the running configuration rather than hard-coding addresses means redeploying the RAN
needs no campaign-config edits, and the report always describes what actually ran.

**Lifecycle constraint, verified on the live stack:** OCUDU buffers its logs and only closes its
pcap files at shutdown ("Closing PCAP files..." on SIGINT). Evidence is therefore incomplete
while the stack is running, so any suite that decodes a pcap must stop (or restart) the product
first. ``teardown`` is what makes the evidence readable, not merely a cleanup step.

Endpoint semantics (``node_endpoint``) are per product class, because only the CU-CP listens:
``cucp`` -> its F1-C SCTP listener; ``cuup``/``du`` -> their GTP-U (F1-U) socket.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ranbench.adapters.base import RanAdapter
from ranbench.config import expand_user_path

# product class -> (binary/process name, human label)
NODES = {
    "cucp": ("ocucp", "O-CU-CP"),
    "cuup": ("ocuup", "O-CU-UP"),
    "du":   ("odu",   "O-DU"),
}

F1C_SCTP_PORT = 38472       # TS 38.472
E1_SCTP_PORT = 38462        # TS 38.462
GTPU_UDP_PORT = 2152        # TS 29.281
NGAP_SCTP_PORT = 38412      # TS 38.412 (N2)


class Adapter(RanAdapter):
    name = "ocudu"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.ran.extra
        self.bin_dir = expand_user_path(e.get("bin_dir", "~/ocudu/build/apps"))
        # explicit per-target binary paths win over the conventional layout
        self.bins: dict[str, Path] = {}
        for tgt, (proc, _) in NODES.items():
            override = (e.get("binaries") or {}).get(tgt)
            self.bins[tgt] = (expand_user_path(override) if override
                              else self.bin_dir / _APP_DIR[tgt] / proc)
        self.configs = {k: expand_user_path(v) for k, v in (e.get("configs") or {}).items()}
        self.use_sudo = bool(e.get("sudo", True))
        self._yaml_cache: dict[str, dict] = {}

    # --- process plumbing -----------------------------------------------------
    def _run(self, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
        cmd = (["sudo", "-n"] if self.use_sudo else []) + list(args)
        self.store.record_command(" ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)

    def _pid(self, target: str) -> str:
        proc = NODES[target][0]
        r = subprocess.run(["pgrep", "-x", proc], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=10)
        return r.stdout.split("\n")[0].strip() if r.returncode == 0 else ""

    def _cfg_yaml(self, target: str) -> dict:
        """The YAML this product was started with, parsed once per campaign."""
        if target in self._yaml_cache:
            return self._yaml_cache[target]
        out: dict = {}
        path = self.configs.get(target)
        if path and Path(path).exists():
            try:
                import yaml
                out = yaml.safe_load(Path(path).read_text()) or {}
            except Exception as exc:  # noqa: BLE001, a bad config must not kill the run
                out = {"_parse_error": f"{type(exc).__name__}: {exc}"}
        self._yaml_cache[target] = out
        return out

    def _version(self, target: str) -> str:
        binary = self.bins.get(target)
        if not binary or not Path(binary).exists():
            return ""
        try:
            r = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=15)
        except Exception:  # noqa: BLE001
            return ""
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                return line.strip().strip("-= ")
        return ""

    # --- the RanAdapter contract ----------------------------------------------
    def describe(self) -> dict[str, Any]:
        facts: dict[str, Any] = {"ran": "OCUDU", "adapter": self.name, "mode": "cu-du-split"}
        up, built = [], []
        for tgt, (_, label) in NODES.items():
            if Path(self.bins[tgt]).exists():
                built.append(tgt)
            if self._pid(tgt):
                up.append(tgt)
        facts["nodes_built"] = ", ".join(built) if built else "(none found)"
        facts["nodes_up"] = ", ".join(up) if up else "(none running)"
        ver = self._version("cucp") or self._version("du")
        if ver:
            facts["ran_release"] = ver
        return facts

    def node_endpoint(self, target: str) -> str:
        ep = self.cfg.ran.endpoints.get(target, "")
        if ep:
            return ep
        y = self._cfg_yaml(target)
        if target == "cucp":
            addr = _dig(y, "cu_cp", "f1ap", "bind_addrs") or _dig(y, "cu_cp", "amf", "bind_addrs")
            return f"{_first(addr)}:{F1C_SCTP_PORT}" if addr else ""
        if target == "cuup":
            addr = _socket_bind(_dig(y, "cu_up", "f1u", "socket")) or \
                _socket_bind(_dig(y, "cu_up", "ngu", "socket"))
            return f"{addr}:{GTPU_UDP_PORT}" if addr else ""
        if target == "du":
            addr = _socket_bind(_dig(y, "f1u", "socket"))
            return f"{addr}:{GTPU_UDP_PORT}" if addr else ""
        return ""

    def node_alive(self, target: str) -> bool | None:
        if target not in NODES:
            return None
        if not shutil.which("pgrep"):
            return None
        return bool(self._pid(target))

    def node_log_grep(self, target: str, patterns: list[str], tail: int = 800) -> bool | None:
        """OCUDU logs to the file named in its config's ``log.filename``."""
        path = _dig(self._cfg_yaml(target), "log", "filename")
        if not path or not Path(path).exists():
            return None
        try:
            lines = Path(path).read_text(errors="ignore").splitlines()[-tail:]
        except OSError:
            return None
        blob = "\n".join(lines).lower()
        return any(p.lower() in blob for p in patterns)

    # --- lifecycle ------------------------------------------------------------
    # The stack is started and stopped around the measurement rather than left running: it is
    # expensive at idle (the DU alone sits near 200% CPU in ZMQ blocking mode), and, crucially,
    # OCUDU only closes its pcap files on shutdown, so stopping a product is what makes its
    # evidence readable.
    def start(self, target: str) -> bool:
        """Start one product class from its configured YAML. False if already running or the
        binary/config is missing. Never raises, a failure to start must surface as 'na'."""
        if self.node_alive(target):
            return False
        binary, cfg_path = self.bins.get(target), self.configs.get(target)
        if not binary or not Path(binary).exists() or not cfg_path or not Path(cfg_path).exists():
            return False
        cmd = (["sudo", "-n"] if self.use_sudo else []) + [str(binary), "-c", str(cfg_path)]
        self.store.record_command(" ".join(cmd))
        console = self.store.raw / f"ocudu-{target}-console.log"
        try:
            with open(console, "w") as fh:
                subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return False
        return True

    def stop(self, target: str, graceful: bool = True) -> None:
        """Stop one product class.

        Graceful (SIGINT) is the default because it is what makes OCUDU close its pcaps, and
        because on F1 it sends an orderly F1 Removal. That matters: a test for *recovery* has to
        take the peer away abruptly (``graceful=False``), otherwise the DU is simply being told
        to stand down and has nothing to recover from.
        """
        proc = NODES.get(target, (None,))[0]
        if not proc:
            return
        self._run("pkill", "-INT" if graceful else "-KILL", "-x", proc, timeout=10)

    def wait_for_log(self, target: str, needle: str, timeout: float = 40.0) -> bool:
        """Block until ``needle`` appears in the product's log file, or the timeout expires.

        **Do not judge a product on this.** OCUDU block-buffers its log, and how much is visible
        while it runs depends only on how chatty the product is. Measured on this rig: the O-DU
        exceeds the buffer quickly and its log tracks reality, while the O-CU-CP had written
        zero bytes after 25 seconds of running with its N2 association established, and only
        flushed at SIGINT. Anything asked of a CU-CP log while it runs therefore answers "no",
        whatever the product actually did. Use ``has_association`` / ``wait_for_association`` /
        ``wait_for_listen`` for anything that decides a verdict.
        """
        path = _dig(self._cfg_yaml(target), "log", "filename")
        if not path:
            return False
        deadline = time.time() + timeout
        p = Path(path)
        while time.time() < deadline:
            try:
                if p.exists() and needle.lower() in p.read_text(errors="ignore").lower():
                    return True
            except OSError:
                pass
            time.sleep(0.5)
        return False

    def has_association(self, port: int) -> bool:
        """Is an SCTP association ESTABLISHED on this port right now?

        A point observation, for deciding whether something that was up has gone away. Reading
        the socket is the only reliable way to ask OCUDU anything about its state while it runs:
        the O-CU-CP block-buffers its log and writes nothing at all until it exits, so a marker
        line for an event that has genuinely happened is simply not in the file yet.
        """
        r = self._run("ss", "-an", "--sctp", timeout=10)
        return any("ESTAB" in line and f":{port}" in line
                   for line in (r.stdout or "").splitlines())

    def wait_for_association(self, port: int, timeout: float = 60.0) -> bool:
        """Block until an SCTP association is ESTABLISHED on a port, or the timeout expires.

        Preferred over waiting for a log line. OCUDU buffers its logs, so a procedure can have
        completed well before the line that announces it reaches the file, and polling the log
        then reports failure for something that actually worked. The socket state cannot lie.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self._run("ss", "-an", "--sctp", timeout=10)
            for line in (r.stdout or "").splitlines():
                if "ESTAB" in line and f":{port}" in line:
                    return True
            time.sleep(1)
        return False

    def wait_for_listen(self, port: int, timeout: float = 45.0) -> bool:
        """Block until something is LISTENing on an SCTP port, or the timeout expires.

        The CU-CP is the only listener in the split, and the O-DU and O-CU-UP are its clients.
        Starting a client before that listener is bound gets it "Connection refused" and the
        product exits, so the whole run then measures a stack that never assembled. Judged on
        the socket for the same reason as ``wait_for_association``: OCUDU buffers its logs, and
        its own start-up lines can reach the file minutes later, at shutdown.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self._run("ss", "-anl", "--sctp", timeout=10)
            for line in (r.stdout or "").splitlines():
                if "LISTEN" in line and f":{port}" in line:
                    return True
            time.sleep(1)
        return False

    def interface_addrs(self, iface: str) -> list[str]:
        """The addresses a named RAN interface actually runs between, from the running configs.

        Needed to judge transport protection honestly. Whether an interface is exposed at all is
        a property of how the operator deployed the products, not of the products: this rig runs
        F1 and E1 between loopback addresses on one host, while N2 crosses a real network. An
        interface that never leaves the host cannot be observed by anyone who is not already
        root on it, and its protection is neither exercised nor measurable.
        """
        key = iface.upper()
        out: list[str] = []
        cu_cp = self._cfg_yaml("cucp").get("cu_cp") or {}
        if "N2" in key:
            amf = cu_cp.get("amf") or {}
            out += [_first(amf.get("bind_addrs")), _first(amf.get("addrs"))]
        if "F1-C" in key:
            out += [_first((cu_cp.get("f1ap") or {}).get("bind_addrs")),
                    _first(_dig(self._cfg_yaml("du"), "f1ap", "bind_addrs"))]
        if "E1" in key:
            out += [_first((cu_cp.get("e1ap") or {}).get("bind_addrs")),
                    _first(_dig(self._cfg_yaml("cuup"), "cu_up", "e1ap", "addrs"))]
        if "F1-U" in key:
            out += [_socket_bind(_dig(self._cfg_yaml("du"), "f1u", "socket")),
                    _socket_bind(_dig(self._cfg_yaml("cuup"), "cu_up", "f1u", "socket"))]
        if "N3" in key:
            out += [_socket_bind(_dig(self._cfg_yaml("cuup"), "cu_up", "ngu", "socket"))]
        return [a for a in out if a]

    def pcap_paths(self, target: str) -> dict[str, str]:
        """The per-interface pcaps this product writes, from its own ``pcap:`` config block.

        Only pcaps whose ``<iface>_enable`` is true are reported, a disabled capture is
        missing evidence, and the test that needs it must grade 'na', not guess.
        """
        pcap = (self._cfg_yaml(target).get("pcap") or {})
        out: dict[str, str] = {}
        for key, value in pcap.items():
            if not key.endswith("_filename"):
                continue
            iface = key[: -len("_filename")]
            if pcap.get(f"{iface}_enable") is True:
                out[iface] = str(value)
        return out


# apps/<dir>/<binary> in the OCUDU build tree
_APP_DIR = {"cucp": "cu_cp", "cuup": "cu_up", "du": "du"}


def _dig(d: Any, *keys: str) -> Any:
    """Walk nested mappings, returning None the moment the path stops existing."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _first(value: Any) -> str:
    """OCUDU accepts a scalar or a list for *_addrs; take the first either way."""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value or "")


def _socket_bind(sockets: Any) -> str:
    """Pull ``bind_addr`` out of an OCUDU ``socket:`` list (F1-U / NG-U)."""
    if isinstance(sockets, (list, tuple)) and sockets:
        first = sockets[0]
        if isinstance(first, dict):
            return str(first.get("bind_addr", "") or "")
    if isinstance(sockets, dict):
        return str(sockets.get("bind_addr", "") or "")
    return ""
