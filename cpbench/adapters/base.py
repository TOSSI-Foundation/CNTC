"""CoreAdapter — the contract every 5G-core distribution plugin implements.

This is the swappable per-core layer (free5gc / open5gs / oai). The suites and the rest of the
engine only ever talk to this interface, never to a specific core. Unlike the UPF adapter
(one UPF), a core adapter fronts a *set* of NFs, so it also resolves each NF's reachable
endpoint and provisions subscribers (5G-AKA needs matching SIM creds in the UDM/UDR).
"""
from __future__ import annotations

import abc
import importlib
from typing import Any


class CoreAdapter(abc.ABC):
    name: str = "base"

    def __init__(self, cfg, store):
        self.cfg = cfg          # config.Campaign
        self.store = store      # cntc_common Store (command capture / raw artifacts)

    # --- lifecycle (default: connect to an already-running core) --------------
    def deploy(self) -> None:
        """Bring the core up. Default: assume it is already running (connect-only)."""

    def teardown(self) -> None:
        """Tear the core down. Default: leave it running."""

    def reset(self) -> None:
        """Return the core to a clean state (clear registrations/sessions). Default: no-op."""

    # --- introspection -> report SUT section ----------------------------------
    @abc.abstractmethod
    def describe(self) -> dict[str, Any]:
        """Return live SUT facts (core name, release, which NFs are Up, images)."""

    # --- endpoint resolution --------------------------------------------------
    def nf_endpoint(self, nf: str) -> str:
        """Reachable ``host:port`` for a given NF's SBI/N2 endpoint. Default: read the
        config's ``core.endpoints`` map; adapters may resolve it live (k8s svc / docker)."""
        return self.cfg.core.endpoints.get(nf, "")

    # --- subscriber provisioning (5G-AKA needs UDM/UDR to know the SIM) --------
    def provision_subscribers(self, subscribers: list[dict[str, Any]]) -> dict[str, Any]:
        """Provision SUPI/Ki/OPc into the core's subscriber DB. Default: assume the operator
        pre-provisioned them (return not-provisioned so ``doctor`` can flag it)."""
        return {"provisioned": 0, "note": "adapter does not auto-provision; assumed pre-set"}

    # --- crash / liveness observation (robustness suites need this) -----------
    def nf_alive(self, nf: str) -> bool | None:
        """Is the NF process still up? Used by NEG-* robustness tests to detect a crash.
        Return None when the adapter cannot observe liveness (graded 'na', never a pass)."""
        return None


def load_adapter(name: str, cfg, store) -> CoreAdapter:
    mod = importlib.import_module(f"cpbench.adapters.{name}")
    cls = getattr(mod, "Adapter")
    return cls(cfg, store)
