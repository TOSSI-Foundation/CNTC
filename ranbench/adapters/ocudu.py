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
