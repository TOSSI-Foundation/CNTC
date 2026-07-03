"""TrafficGenerator — drives packets into the UPF and reports counters.

Implementations:
  * ``testpmd`` — DPDK testpmd, raw-frame line-rate blast (Suite 1, the path we used).
  * ``trex``    — Cisco TRex, GTP-U with multi-TEID per-UE flows (Suites 1 & 2).

One ``Trial`` runs steady-state at a target offered load and returns absorbed/forwarded
counts so the caller (binary search) can compute the loss ratio.
"""
from __future__ import annotations

import abc
import dataclasses
import importlib


@dataclasses.dataclass
class Trial:
    offered_mpps: float               # achieved offered rate (what the generator sent)
    rx_mpps: float                    # absorbed by UPF (N3 ingress port RX)
    tx_mpps: float                    # forwarded by UPF (N6 egress port TX)
    gbps: float = 0.0
    sent_pkts: int = 0                # packets the generator actually injected
    duration_s: float = 0.0           # measured send duration

    @property
    def loss_ratio(self) -> float:
        if self.rx_mpps <= 0:
            return 1.0
        return max(0.0, (self.rx_mpps - self.tx_mpps) / self.rx_mpps)


class TrafficGenerator(abc.ABC):
    name: str = "base"

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    @abc.abstractmethod
    def run_trial(self, *, frame_size: int, offered_mpps: float,
                  duration_s: int = 60, teids: list[int] | None = None,
                  ue_ips: list[str] | None = None,
                  encapsulation: str = "raw") -> Trial:
        """Run one steady-state trial; return measured rates.

        ``teids`` + ``ue_ips`` (parallel lists) define per-UE flows — used by Suite 2
        to match the TEID/UE-IP that pfcpsim assigned each session, so traffic hits the
        real per-UE PDRs. Both omitted = a single default flow.
        """


def load_generator(name: str, cfg, store) -> TrafficGenerator:
    mod = importlib.import_module(f"upfbench.traffic.{name}")
    cls = getattr(mod, "Generator")
    return cls(cfg, store)
