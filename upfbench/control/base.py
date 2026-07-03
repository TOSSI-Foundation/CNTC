"""ControlPlane — how packet-forwarding rules / PFCP sessions get installed.

Two implementations:
  * ``pybess``  — BESS-specific white-box gRPC (the fast path we already use; Suite 1).
  * ``pfcpsim`` — standardized PFCP/N4 via omec-project/pfcpsim (portable; Suites 2 & 3).

A "session" here is one UE's forwarding state (PDR/FAR/QER). Suite 2 creates many;
Suite 3 drives the lifecycle for conformance.
"""
from __future__ import annotations

import abc
import importlib
from typing import Any


class ControlPlane(abc.ABC):
    name: str = "base"

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    @abc.abstractmethod
    def setup(self) -> None:
        """Establish the control channel (gRPC connect / PFCP association)."""

    @abc.abstractmethod
    def install_sessions(self, count: int = 1, base_id: int = 1, **kw) -> dict[str, Any]:
        """Install ``count`` UE sessions. Returns info incl. assigned TEIDs/UE IPs."""

    def aligned_flows(self, count: int = 1, base_id: int = 1):
        """The (teids, ue_ips) that N3 traffic must use to hit the PDRs this control
        installs for ``count`` sessions from ``base_id``. Default ``(None, None)`` means
        the control forwards wildcard (e.g. pybess), so the generator's defaults already
        match; controls that install specific per-session F-TEID/UE-IP PDRs override it."""
        return None, None

    def modify_session(self, session_id: int, **kw) -> dict[str, Any]:
        """Modify one session (CF-03). May be unsupported by the backend."""
        raise NotImplementedError

    @abc.abstractmethod
    def delete_sessions(self, count: int = 1, base_id: int = 1) -> dict[str, Any]:
        ...

    def teardown(self) -> None:
        """Release the control channel (disassociate)."""


def load_control(name: str, cfg, store) -> ControlPlane:
    mod = importlib.import_module(f"upfbench.control.{name}")
    cls = getattr(mod, "Control")
    return cls(cfg, store)
