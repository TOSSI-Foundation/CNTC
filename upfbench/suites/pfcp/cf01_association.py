"""CF-01 PFCP association setup / release — Suite 3 (PFCP conformance, 3GPP TS 29.244)."""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult


def needs_pfcpsim(ctx) -> bool:
    return ctx.control is None or not hasattr(ctx.control, "associate")


class Cf01Association(TestCase):
    id, name = "CF-01", "PFCP association setup / release"

    def run(self, ctx: RunContext) -> TestResult:
        if needs_pfcpsim(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        a = ctx.control.associate()
        d = ctx.control.disassociate()
        # A UPF may not implement graceful Association Release (TS 29.244 §7.4.5) —
        # e.g. OAI-UPF tears associations down only on heartbeat loss. That is a
        # documented capability gap, so it does not fail the setup conformance check.
        if d.get("unsupported"):
            return TestResult(self.id, self.name, "pass" if a["ok"] else "fail",
                              metrics={"associate_ok": a["ok"], "release_ok": "n/a"},
                              notes="Association Setup Request/Response verified (TS 29.244 §7.4). "
                                    "Association Release not supported by this UPF — not asserted.")
        ok = a["ok"] and d["ok"]
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"associate_ok": a["ok"], "release_ok": d["ok"]},
                          notes="Association Setup Request/Response + Release (TS 29.244 §7.4).")


TESTS = [Cf01Association]
