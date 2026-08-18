"""XDP-04: an installed PFCP session is reflected in the eBPF maps (TS 29.244 §5.2).

The core control→dataplane binding proof: install one session over N4 (pfcpsim) and confirm its
F-TEID appears as a PDR in the eBPF map. This is what "the UPF applies the PDRs/FARs it receives
over N4" means, verified white-box rather than inferred from forwarding. Essential."""
import time

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.ebpf._common import not_ebpf, needs_control, intro, na, install_sessions, delete_sessions

_BASE = 700701


class Xdp04Bind(TestCase):
    id, name = "XDP-04", "Installed PFCP session (PDR/FAR) is reflected in the eBPF maps"

    def run(self, ctx: RunContext) -> TestResult:
        if not_ebpf(ctx):
            return na(self.id, self.name, "not an eBPF/XDP UPF")
        if needs_control(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        before = intro(ctx)
        if not before:
            return na(self.id, self.name, "adapter exposes no BPF introspection")
        teids, _ = install_sessions(ctx, 1, _BASE)
        teid = int(teids[0])
        time.sleep(1)
        after = intro(ctx)
        try:
            bound = teid in (after.get("teids") or [])
            pdr_before = int(before["maps"]["pdr"]["entries"])
            pdr_after = int(after["maps"]["pdr"]["entries"])
            grew = pdr_after > pdr_before
        finally:
            delete_sessions(ctx, 1, _BASE)
        ok = bound and grew
        return TestResult(self.id, self.name, "pass" if ok else "fail",
            metrics={"teid": teid, "teid_in_map": bound,
                     "pdr_entries_before": pdr_before, "pdr_entries_after": pdr_after,
                     "teids_after": after.get("teids")},
            notes=(f"Installed TEID {teid} {'APPEARS' if bound else 'is MISSING'} in the eBPF PDR "
                   f"map; PDR entries {pdr_before}->{pdr_after} (TS 29.244 §5.2)."))


TESTS = [Xdp04Bind]
