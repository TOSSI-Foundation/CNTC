"""UPFAdapter — the contract every UPF plugin implements.

This is the swappable per-UPF layer. The first implementation is ``sdcore_bess``;
OAI / Open5GS / free5GC / eUPF are added by dropping in new modules here. The suites
and the rest of the framework only ever talk to this interface, never to a specific UPF.
"""
from __future__ import annotations

import abc
import importlib
from typing import Any


class UPFAdapter(abc.ABC):
    name: str = "base"

    def __init__(self, cfg, store):
        self.cfg = cfg          # config.UpfConfig
        self.store = store      # results.Store (for command capture / raw artifacts)

    # --- lifecycle (optional: Phase-0 connects to an already-running UPF) -----
    def deploy(self) -> None:
        """Bring the UPF up. Default: assume it is already running (connect-only)."""

    def teardown(self) -> None:
        """Tear the UPF down. Default: leave it running."""

    def reset(self) -> None:
        """Return the UPF to a clean datapath/session state, blocking until ready.

        Heavy session churn and saturating traffic degrade a UPF's datapath over a
        long run (e.g. BESS ``bessd`` can crash under saturation; OAI's session table
        wedges after the performance suite), so a back-to-back ``--suite all`` run can
        see one suite poison the next. When ``reset_between_suites`` is set, the runner
        calls this before each suite so every suite starts from a fresh UPF — matching
        the "start from a clean UPF for trustworthy numbers" guidance.

        Default: no-op (connect-only UPFs that need no reset). Adapters override with
        the cheapest full reset they have (pod delete / container restart) and must
        return only once the UPF is ready to serve N4/N3 again.
        """

    # --- introspection -> fills the report's System-Under-Test section --------
    @abc.abstractmethod
    def describe(self) -> dict[str, Any]:
        """Return SUT facts the adapter can read live (image digest, driver, mode...)."""

    # --- counters -> measurement plane ---------------------------------------
    @abc.abstractmethod
    def port_counters(self) -> dict[str, dict[str, int]]:
        """Per-port packet/byte/drop counters for loss math.

        Returns ``{port_name: {rx_pkts, rx_bytes, rx_drops, tx_pkts, tx_bytes,
        tx_drops}}``. A UPF has at least an N3 (access) and N6 (core) port, so
        counters are keyed by port — the throughput suite picks the N3 ingress
        and N6 egress ports to compute absorbed/forwarded/loss. ``rx`` = packets
        the UPF received on that port (from the wire); ``tx`` = packets it sent
        out that port.
        """

    # --- counter semantics (overridable per UPF) ------------------------------
    def fwd_field(self) -> str:
        """Which N6 counter field counts *uplink-forwarded* packets.

        For physical/BESS egress ports the UPF transmits the decapsulated packet,
        so forwarded == ``tx_pkts`` (the default). For TUN-based datapaths (e.g.
        OAI-UPF simpleswitch) the UPF injects the decapsulated packet *into* the
        kernel via the tun device, so from the interface's view it appears as
        ``rx_pkts``. Adapters whose N6 is a tun override this.
        """
        return "tx_pkts"


def load_adapter(name: str, cfg, store) -> UPFAdapter:
    mod = importlib.import_module(f"upfbench.adapters.{name}")
    cls = getattr(mod, "Adapter")
    return cls(cfg, store)
