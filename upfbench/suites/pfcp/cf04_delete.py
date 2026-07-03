"""CF-04 Session deletion — Suite 3 (PFCP conformance, 3GPP TS 29.244)."""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.pfcp.cf01_association import needs_pfcpsim

_BASE = 30


class Cf04Delete(TestCase):
    id, name = "CF-04", "Session deletion"

    def run(self, ctx: RunContext) -> TestResult:
        if needs_pfcpsim(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        ctx.control.ensure_associated()
        est = ctx.control.create_sessions(count=1, base_id=_BASE)
        dele = ctx.control.delete_sessions_raw(count=1, base_id=_BASE)
        ok = est["ok"] and dele["ok"]
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"establish_ok": est["ok"], "delete_ok": dele["ok"]},
                          notes="Session Deletion Request on an established session; UPF "
                                "removes context and returns success (TS 29.244 §7.5.6).")


TESTS = [Cf04Delete]
