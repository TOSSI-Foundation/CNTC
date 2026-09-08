"""RanAdapter: the contract every RAN distribution plugin implements.

This is the swappable per-stack layer (ocudu / oai / ...). The suites and the rest of the engine
only ever talk to this interface, never to a specific stack. Like the core adapter in
``cpbench`` it fronts a *set* of products (O-CU-CP, O-CU-UP, O-DU), so it resolves each one's
reachable endpoint and observes its liveness.

Two additions over the core adapter:

* ``pcap_paths``. The per-interface captures (NGAP, F1AP, E1AP, F1-U, N3) are the primary
  evidence for the protocol and AS-security tests. Where they come from is the adapter's
  business and nobody above it can tell: a stack that writes its own captures reports the files
  it wrote, and a stack that writes none has them captured off the wire on its behalf. The
  observer and the test cases are identical either way.
* ``log_paths``. Product logs are kept as diagnosis, never as verdict, but they still have to be
  copied into the campaign before the next run overwrites them.

The socket observations below are deliberately *concrete* rather than abstract. Asking whether
an SCTP association is established is a question about the operating system, not about any
vendor's product, so every adapter gets one correct implementation instead of reimplementing it
and drifting. What genuinely differs per stack, starting a product and finding its logs, stays
abstract.
"""
from __future__ import annotations

import abc
import importlib
import subprocess
import time
from typing import Any


class RanAdapter(abc.ABC):
    name: str = "base"

    def __init__(self, cfg, store):
        self.cfg = cfg          # config.Campaign
        self.store = store      # cntc_common Store (command capture / raw artifacts)
        self.use_sudo = bool(cfg.ran.extra.get("sudo", True))

    # --- process plumbing -----------------------------------------------------
    def _run(self, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
        """Run one command, recorded in the campaign so a reader can repeat it."""
        cmd = (["sudo", "-n"] if self.use_sudo else []) + list(args)
        self.store.record_command(" ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)

    # --- lifecycle (default: connect to an already-running stack) --------------
    def deploy(self) -> None:
        """Bring the RAN up. Default: assume it is already running (connect-only)."""

    def teardown(self) -> None:
        """Tear the RAN down. Default: leave it running."""

    def reset(self) -> None:
        """Return the RAN to a clean state. Default: no-op."""

    def start(self, target: str) -> bool:
        """Start one product class. False when it cannot be started, never raises.

        A stack the adapter does not manage returns False, and the engine then measures whatever
        is already running rather than pretending it launched something.
        """
        return False

    def stop(self, target: str, graceful: bool = True) -> None:
        """Stop one product class.

        ``graceful`` matters to more than tidiness. A graceful stop is an orderly shutdown, so a
        peer is being *told* to stand down and has nothing to recover from; a test for recovery
        has to take the peer away abruptly. Some stacks also only close their captures on a
        graceful stop, which is what makes their evidence readable.
        """

    # --- introspection -> report SUT section ----------------------------------
    @abc.abstractmethod
    def describe(self) -> dict[str, Any]:
        """Live SUT facts (stack name, release/commit, which products are up, split mode)."""

    # --- endpoint resolution --------------------------------------------------
    def node_endpoint(self, target: str) -> str:
        """Reachable ``host:port`` for a target's control-plane link (CU-CP: F1-C, CU-UP: E1,
        DU: its F1-C peer). Default: read the config; adapters should resolve it live."""
        return self.cfg.ran.endpoints.get(target, "")

    def interface_addrs(self, iface: str) -> list[str]:
        """The addresses a named interface ("N2", "F1-C", "E1", "F1-U", "N3") runs between.

        Needed to judge transport protection honestly. Whether an interface is exposed at all is
        a property of how the operator deployed the products, not of the products: an interface
        that never leaves the host cannot be observed by anyone who is not already root on it,
        so its protection is neither exercised nor measurable. Empty means unknown, and the
        affected test grades 'na' rather than guessing.
        """
        return []

    def amf_address(self) -> str:
        """The AMF the CU-CP is configured to reach, read from the running configuration.

        Asked by the N2 tests and by the diagnosis, which both need to distinguish "the product
        failed to associate" from "the AMF it was pointed at was never there". Empty when the
        adapter cannot tell, and the caller then declines to make the claim.
        """
        return ""

    # --- liveness (robustness suites need this) -------------------------------
    def node_alive(self, target: str) -> bool | None:
        """Is the product's process still up? Used by NEG-* tests to detect a crash. Return
        None when the adapter cannot observe liveness (graded 'na', never a pass)."""
        return None

    def node_log_grep(self, target: str, patterns: list[str], tail: int = 800) -> bool | None:
        """Whether the target's recent logs contain any of ``patterns`` (case-insensitive).
        None when the logs cannot be read."""
        return None

    def wait_for_log(self, target: str, needle: str, timeout: float = 40.0) -> bool:
        """Block until ``needle`` appears in the product's log, or the timeout expires.

        **Never decide a verdict on this.** A log line says what a product chose to write and
        when it chose to flush it, not what it did. Stacks that block-buffer their logs answer
        "no" to questions about things that have genuinely happened, because the line is not in
        the file yet. Use the socket observations below for anything that grades.
        """
        return False

    # --- socket observation (generic: this is the OS, not the stack) ----------
    def _sctp_lines(self, listening: bool = False) -> list[str]:
        try:
            r = self._run("ss", "-anl" if listening else "-an", "--sctp", timeout=10)
        except (OSError, subprocess.SubprocessError):
            return []
        return (r.stdout or "").splitlines()

    def has_association(self, port: int) -> bool:
        """Is an SCTP association ESTABLISHED on this port right now?

        A point observation, for deciding whether something that was up has gone away. Reading
        the socket is the only account of a running product that cannot be reworded by a release
        or delayed by a log buffer.
        """
        return any("ESTAB" in ln and f":{port}" in ln for ln in self._sctp_lines())

    def wait_for_association(self, port: int, timeout: float = 60.0) -> bool:
        """Block until an SCTP association is ESTABLISHED on a port, or the timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.has_association(port):
                return True
            time.sleep(1)
        return False

    def wait_for_listen(self, port: int, timeout: float = 45.0) -> bool:
        """Block until something is LISTENing on an SCTP port, or the timeout expires.

        Ordering the products matters: a client started before its listener is bound gets
        "Connection refused" and exits, and the run then measures a stack that never assembled.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any("LISTEN" in ln and f":{port}" in ln for ln in self._sctp_lines(listening=True)):
                return True
            time.sleep(1)
        return False

    # --- evidence -------------------------------------------------------------
    def pcap_paths(self, target: str) -> dict[str, str]:
        """``{interface: path}`` for this target's captures (ngap/f1ap/e1ap/f1u/n3).
        Empty when there are none, the affected tests then grade 'na'."""
        return {}

    def log_paths(self, target: str) -> dict[str, str]:
        """``{name: path}`` for the product's own log files, for diagnosis only.

        Kept because a finding whose supporting line cannot be produced afterwards is not much
        of a finding, and stacks routinely truncate their logs at startup.
        """
        return {}


def load_adapter(name: str, cfg, store) -> RanAdapter:
    mod = importlib.import_module(f"ranbench.adapters.{name}")
    cls = getattr(mod, "Adapter")
    return cls(cfg, store)
