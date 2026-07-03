"""Campaign configuration: load + validate the YAML the user provides.

A campaign config names the UPF under test, the System-Under-Test facts that go
verbatim into the report, the suite to run, and the per-suite knobs. See
``configs/sdcore-bess.yaml`` for the canonical example.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

VALID_SUITES = ("performance", "load", "pfcp", "n3neg", "conformance", "all")


@dataclasses.dataclass
class UpfConfig:
    adapter: str                      # which adapters/<name>.py to load
    n3_iface: str = ""                # access PF / N3 ingress
    n6_iface: str = ""                # core PF / N6 egress
    n4_addr: str = ""                 # UPF PFCP agent address (for pfcpsim)
    mode: str = ""                    # dataplane mode label (af_xdp/cndp/dpdk/...)
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Campaign:
    campaign: str                     # campaign id, e.g. UPF-BM-2026-001
    suite: str                        # performance | load | pfcp | all
    sut: dict[str, Any]               # hardware/software facts -> report SUT section
    upf: UpfConfig
    performance: dict[str, Any] = dataclasses.field(default_factory=dict)
    load: dict[str, Any] = dataclasses.field(default_factory=dict)
    pfcp: dict[str, Any] = dataclasses.field(default_factory=dict)
    n3neg: dict[str, Any] = dataclasses.field(default_factory=dict)
    baseline: str | None = None       # path to a prior results.json for comparison
    report_commands: bool = True      # include the captured-commands appendix in the
                                      # combined report-all.pdf (set false for a clean
                                      # share copy; commands stay in results.json)
    reset_between_suites: bool = False  # reset the UPF to a clean state before each
                                        # suite (adapter.reset()); avoids one suite's
                                        # session churn / saturation degrading the next
                                        # in a back-to-back --suite all run
    profile: str = "conformance"        # CNTC requirement profile to grade against
                                        # (conformance | performance); CLI --profile overrides

    @property
    def suites(self) -> list[str]:
        """Expand aggregate selectors into concrete suite lists, preserving run order."""
        if self.suite == "all":
            return ["performance", "load", "pfcp"]
        if self.suite == "conformance":
            # The correctness/certification run: PFCP conformance + N3 robustness. Both
            # feed the CNTC `conformance` profile so its essential gate can reach PASS.
            return ["pfcp", "n3neg"]
        return [self.suite]


def load(path: str | Path) -> Campaign:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    _require(raw, "campaign", str)
    _require(raw, "suite", str)
    if raw["suite"] not in VALID_SUITES:
        raise ValueError(f"suite must be one of {VALID_SUITES}, got {raw['suite']!r}")

    upf_raw = raw.get("upf") or {}
    if "adapter" not in upf_raw:
        raise ValueError("config.upf.adapter is required (e.g. 'sdcore_bess')")
    known = {f.name for f in dataclasses.fields(UpfConfig)}
    upf = UpfConfig(
        adapter=upf_raw["adapter"],
        n3_iface=upf_raw.get("n3_iface", ""),
        n6_iface=upf_raw.get("n6_iface", ""),
        n4_addr=upf_raw.get("n4_addr", ""),
        mode=upf_raw.get("mode", ""),
        extra={k: v for k, v in upf_raw.items() if k not in known},
    )

    return Campaign(
        campaign=raw["campaign"],
        suite=raw["suite"],
        sut=raw.get("sut", {}),
        upf=upf,
        performance=raw.get("performance", {}),
        load=raw.get("load", {}),
        pfcp=raw.get("pfcp", {}),
        n3neg=raw.get("n3neg", {}),
        baseline=raw.get("baseline"),
        report_commands=raw.get("report_commands", True),
        reset_between_suites=raw.get("reset_between_suites", False),
        profile=raw.get("profile", "conformance"),
    )


def _require(d: dict, key: str, typ: type) -> None:
    if key not in d:
        raise ValueError(f"config.{key} is required")
    if not isinstance(d[key], typ):
        raise ValueError(f"config.{key} must be {typ.__name__}")
