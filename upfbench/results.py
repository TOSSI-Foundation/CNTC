"""Campaign result store: one directory per campaign under ``campaigns/``.

Layout::

    campaigns/UPF-BM-2026-001/
        results.json     structured numbers (machine-readable, comparable)
        raw/             raw counters, generator logs, captured commands
        report.pdf       the rendered report

The runner appends a :class:`SuiteResult` per suite executed; ``report`` consumes
``results.json`` and ``baseline`` consumes a prior one for the comparison section.
"""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any


@dataclasses.dataclass
class TestResult:
    """One test case (TC-01, LT-02, CF-03, ...)."""
    id: str
    name: str
    status: str                       # "pass" | "fail" | "measured" | "error"
    metrics: dict[str, Any] = dataclasses.field(default_factory=dict)
    tables: dict[str, list[dict]] = dataclasses.field(default_factory=dict)
    notes: str = ""


@dataclasses.dataclass
class SuiteResult:
    suite: str                        # performance | load | pfcp
    tests: list[TestResult] = dataclasses.field(default_factory=list)


class Store:
    def __init__(self, root: Path, campaign_id: str):
        self.dir = root / campaign_id
        self.raw = self.dir / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.campaign_id = campaign_id
        self._suites: list[SuiteResult] = []
        self._meta: dict[str, Any] = {
            "campaign": campaign_id,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._commands: list[str] = []   # reproducibility appendix
        self._kpis: dict[str, Any] = {}  # headline KPIs for the executive summary
        self._sut_live: dict[str, Any] = {}  # facts read live from the UPF adapter
        self._verdict: dict[str, Any] | None = None  # CNTC graded verdict (optional)

    def add_suite(self, result: SuiteResult) -> None:
        self._suites.append(result)

    def suite_dicts(self) -> list[dict[str, Any]]:
        """The serialized suites (as they appear in results.json) — used by the CNTC
        verdict layer to grade results without reaching into private state."""
        return [dataclasses.asdict(s) for s in self._suites]

    def set_verdict(self, verdict: dict[str, Any]) -> None:
        """Attach a CNTC verdict block (from cntc.verdict.evaluate) to the results."""
        self._verdict = verdict

    def sut_live(self) -> dict[str, Any]:
        return dict(self._sut_live)

    def set_kpi(self, key: str, value: Any) -> None:
        self._kpis[key] = value

    def record_command(self, cmd: str) -> None:
        """Captured for the report's Reproducibility appendix."""
        self._commands.append(cmd)

    def set_sut_live(self, facts: dict[str, Any]) -> None:
        """Facts the UPF adapter read live (mode, image, ports...). Merged over the
        config's static ``sut`` block at save time so the report shows ground truth."""
        self._sut_live.update(facts)

    def save(self, sut: dict[str, Any], status: str = "complete",
             running_suite: str | None = None) -> Path:
        # Config SUT facts (cpu/nic/kernel the adapter can't know) + live facts on top.
        merged_sut = {**sut, **{k: _flatten(v) for k, v in self._sut_live.items()}}
        payload = {
            **self._meta,
            "status": status,               # "running" (incremental) | "complete" — for the live dashboard
            "running_suite": running_suite,  # the suite currently executing, if any
            "sut": merged_sut,
            "kpis": self._kpis,        # headline numbers; populated as suites compute them
            "suites": [dataclasses.asdict(s) for s in self._suites],
            "commands": self._commands,
        }
        if self._verdict is not None:
            payload["verdict"] = self._verdict   # CNTC graded scorecard
        out = self.dir / "results.json"
        out.write_text(json.dumps(payload, indent=2, default=str))
        return out

    @staticmethod
    def load(path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text())


def _flatten(value: Any) -> Any:
    """Coerce a SUT value into something the flat report table can render.

    Scalars pass through; a dict-of-dicts (e.g. per-port ``{accessFast: {driver,
    link, speed}}``) becomes ``"accessFast: PMDPort/UP/10,000Mbps; coreFast: ..."``;
    lists become a comma-joined string.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if isinstance(v, dict):
                v = "/".join(str(x) for x in v.values())
            parts.append(f"{k}: {v}")
        return "; ".join(parts)
    if isinstance(value, (list, tuple)):
        return ", ".join(str(x) for x in value)
    return str(value)
