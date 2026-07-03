"""DPDK testpmd generator (Suite 1, raw-frame line-rate blast)."""
from __future__ import annotations

from upfbench.traffic.base import TrafficGenerator, Trial


class Generator(TrafficGenerator):
    name = "testpmd"

    def run_trial(self, *, frame_size, offered_mpps, duration_s=60,
                  teids=None, encapsulation="raw") -> Trial:
        raise NotImplementedError("phase1: drive testpmd --txonly + read port counters")
