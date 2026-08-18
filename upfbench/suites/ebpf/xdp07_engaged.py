"""XDP-07: the XDP fast path is engaged: stats increment under N3 traffic.

Attachment alone doesn't prove packets traverse XDP. Install a session, blast valid GTP-U on its
TEID at the XDP hook, and confirm the XDP counters move, GTP-U PDUs seen AND packets forwarded
(XDP_TX/REDIRECT). This is the real forwarded-packet proof for the eBPF datapath. Essential."""
import time

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.ebpf._common import (not_ebpf, needs_control, intro, na,
                                          install_sessions, delete_sessions, inject_gtpu)

_BASE = 701001


class Xdp07Engaged(TestCase):
    id, name = "XDP-07", "XDP fast-path engaged, stats map increments under N3 traffic (packets traverse XDP)"

    def run(self, ctx: RunContext) -> TestResult:
        if not_ebpf(ctx):
            return na(self.id, self.name, "not an eBPF/XDP UPF")
        if needs_control(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        if not intro(ctx):
            return na(self.id, self.name, "adapter exposes no BPF introspection")
        n = int(ctx.knobs.get("inject_pkts", 1000))
        teids, ue_ips = install_sessions(ctx, 1, _BASE)
        teid, ue = int(teids[0]), ue_ips[0]
        before = intro(ctx)["stats"]
        sent = inject_gtpu(ctx, teid, ue, n)
        time.sleep(1)
        after = intro(ctx)["stats"]
        try:
            d_rx = int(after["rx_gtp_pdu"]) - int(before["rx_gtp_pdu"])
            d_fwd = ((int(after["tx_packets"]) - int(before["tx_packets"]))
                     + (int(after["xdp_redirect"]) - int(before["xdp_redirect"])))
        finally:
            delete_sessions(ctx, 1, _BASE)
        ok = sent > 0 and d_rx > 0 and d_fwd > 0
        return TestResult(self.id, self.name, "pass" if ok else "fail",
            metrics={"teid": teid, "sent": sent, "rx_gtp_pdu_delta": d_rx, "forwarded_delta": d_fwd},
            notes=(f"sent {sent} GTP-U on TEID {teid}; XDP saw {d_rx} GTP-U PDUs and forwarded "
                   f"{d_fwd} (XDP_TX/REDIRECT). Fast path engaged." if ok
                   else f"XDP counters did not advance as expected: rx_gtp_pdu+{d_rx}, forwarded+{d_fwd}."))


TESTS = [Xdp07Engaged]
