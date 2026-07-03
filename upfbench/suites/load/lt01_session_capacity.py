"""LT-01 Max concurrent UE sessions (capacity ceiling) — Suite 2 (multi-UE load).

Control-plane only: pfcpsim installs increasing batches of real PFCP sessions (one
PDR/FAR/QER set per UE) and we record how many the UPF accepts and how fast. The
ceiling is the largest batch that establishes cleanly (bounded by the UPF's
max_sessions). No traffic is needed for this test.
"""
from __future__ import annotations

import time

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult


def _needs_pfcpsim(ctx) -> bool:
    return ctx.control is None or not hasattr(ctx.control, "create_sessions")


class Lt01SessionCapacity(TestCase):
    id, name = "LT-01", "Max concurrent UE sessions (capacity ceiling)"

    def run(self, ctx: RunContext) -> TestResult:
        if _needs_pfcpsim(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        ue_counts = ctx.knobs.get("ue_counts", [10, 100, 1000, 5000])
        ctx.control.ensure_associated()

        rows, ceiling = [], 0
        for n in ue_counts:
            t0 = time.time()
            est = ctx.control.create_sessions(count=n, base_id=1)
            dt = time.time() - t0
            ctx.control.delete_sessions_raw(count=n, base_id=1)   # clean up before next batch
            ok = est["ok"]
            rows.append({"ue_count": n, "established": ok,
                         "install_s": round(dt, 3),
                         "sessions_per_s": round(n / dt) if dt > 0 else 0})
            if ok:
                ceiling = max(ceiling, n)
            else:
                break   # first batch the UPF can't take = ceiling reached

        ctx.store.set_kpi("max_sessions", ceiling)
        return TestResult(self.id, self.name, status="measured",
                          metrics={"capacity_sessions": ceiling},
                          tables={"Session capacity ramp": rows},
                          notes="pfcpsim batches of real PFCP sessions; ceiling = largest "
                                "batch the UPF establishes (bounded by UPF max_sessions).")


TESTS = [Lt01SessionCapacity]
