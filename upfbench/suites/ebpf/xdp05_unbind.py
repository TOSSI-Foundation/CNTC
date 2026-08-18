"""XDP-05: deleting a PFCP session removes its eBPF map entries (no stale dataplane state).

The unbinding half of TS 29.244 §5.2: after an N4 Session Deletion the UPF must drop the rule
from the fast path, or it would keep forwarding for a torn-down session (a correctness and
security defect). Install, confirm the TEID is present, delete, confirm it is gone. Essential."""
import time

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.ebpf._common import not_ebpf, needs_control, intro, na, install_sessions, delete_sessions

_BASE = 700801


class Xdp05Unbind(TestCase):
    id, name = "XDP-05", "Deleted PFCP session removes its eBPF map entries (no stale dataplane state)"

    def run(self, ctx: RunContext) -> TestResult:
        if not_ebpf(ctx):
            return na(self.id, self.name, "not an eBPF/XDP UPF")
        if needs_control(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        if not intro(ctx):
            return na(self.id, self.name, "adapter exposes no BPF introspection")
        teids, _ = install_sessions(ctx, 1, _BASE)
        teid = int(teids[0])
        time.sleep(1)
        present = teid in (intro(ctx).get("teids") or [])
        delete_sessions(ctx, 1, _BASE)
        time.sleep(1)
        after = intro(ctx)
        gone = teid not in (after.get("teids") or [])
        ok = present and gone
        return TestResult(self.id, self.name, "pass" if ok else "fail",
            metrics={"teid": teid, "present_after_install": present, "gone_after_delete": gone,
                     "teids_after_delete": after.get("teids")},
            notes=(f"TEID {teid}: present-after-install={present}, gone-after-delete={gone}. "
                   f"{'No stale dataplane state.' if ok else 'STALE entry remained after delete.'}"))


TESTS = [Xdp05Unbind]
