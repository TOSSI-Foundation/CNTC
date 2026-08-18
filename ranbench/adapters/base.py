"""RanAdapter: the contract every RAN distribution plugin implements.

This is the swappable per-stack layer (ocudu / oai / …). The suites and the rest of the engine
only ever talk to this interface, never to a specific stack. Like the core adapter in
``cpbench`` it fronts a *set* of products (O-CU-CP, O-CU-UP, O-DU), so it resolves each one's
reachable endpoint and observes its liveness.

One addition over the core adapter: ``pcap_paths``. A modern CU/DU stack writes per-interface
pcaps itself (NGAP, F1AP, E1AP, F1-U, N3), which is the primary evidence for the protocol and
AS-security tests. The adapter is what knows where those files land.
"""
from __future__ import annotations

import abc
import importlib
from typing import Any


class RanAdapter(abc.ABC):
    name: str = "base"

    def __init__(self, cfg, store):
        self.cfg = cfg          # config.Campaign
        self.store = store      # cntc_common Store (command capture / raw artifacts)

    # --- lifecycle (default: connect to an already-running stack) --------------
    def deploy(self) -> None:
        """Bring the RAN up. Default: assume it is already running (connect-only)."""

    def teardown(self) -> None:
        """Tear the RAN down. Default: leave it running."""

    def reset(self) -> None:
        """Return the RAN to a clean state. Default: no-op."""

    # --- introspection -> report SUT section ----------------------------------
    @abc.abstractmethod
    def describe(self) -> dict[str, Any]:
        """Live SUT facts (stack name, release/commit, which products are up, split mode)."""

    # --- endpoint resolution --------------------------------------------------
    def node_endpoint(self, target: str) -> str:
        """Reachable ``host:port`` for a target's control-plane link (CU-CP: F1-C, CU-UP: E1,
        DU: its F1-C peer). Default: read the config; adapters should resolve it live."""
        return self.cfg.ran.endpoints.get(target, "")

    # --- liveness (robustness suites need this) -------------------------------
    def node_alive(self, target: str) -> bool | None:
        """Is the product's process still up? Used by NEG-* tests to detect a crash. Return
        None when the adapter cannot observe liveness (graded 'na', never a pass)."""
        return None

    def node_log_grep(self, target: str, patterns: list[str], tail: int = 800) -> bool | None:
        """Whether the target's recent logs contain any of ``patterns`` (case-insensitive).
        None when the logs cannot be read."""
        return None

    # --- evidence -------------------------------------------------------------
    def pcap_paths(self, target: str) -> dict[str, str]:
        """``{interface: path}`` for the pcaps this target writes (ngap/f1ap/e1ap/f1u/n3).
        Empty when the stack writes none, the affected tests then grade 'na'."""
        return {}


def load_adapter(name: str, cfg, store) -> RanAdapter:
    mod = importlib.import_module(f"ranbench.adapters.{name}")
    cls = getattr(mod, "Adapter")
    return cls(cfg, store)
