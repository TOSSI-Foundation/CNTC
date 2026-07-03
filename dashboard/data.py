"""Load + normalize campaign results for the dashboard.

The framework writes one ``campaigns/<name>/results.json`` per run. This module scans that
directory, parses each file into a small typed model, and exposes helpers the pages use.
It is the single source of truth for the dashboard — every page reads from here, never from
disk directly. Robust by design: a campaign with no/partial/corrupt ``results.json`` is
skipped, not fatal.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# A run counts as a "verified" (credible) result if its campaign id matches one of these
# and it has real data; everything else is "experimental" (dev/debug) and grouped apart.
_VERIFIED_RE = re.compile(r"^(UPF-BM-|W4-ALL|VERIFY-)", re.I)

# Repo root = parent of the dashboard package; campaigns/ lives beside upfbench/.
ROOT = Path(__file__).resolve().parent.parent
CAMPAIGNS_DIR = ROOT / "campaigns"

# Friendly suite labels + ordering for display.
SUITE_ORDER = ["performance", "load", "pfcp", "n3neg"]
SUITE_LABEL = {
    "performance": "Performance",
    "load": "Multi-UE Load",
    "pfcp": "PFCP Conformance",
    "n3neg": "N3 Robustness",
}
# A test "passed" for the green/amber/red summary if its status is one of these.
GOOD = {"pass", "measured"}
BAD = {"fail", "error"}


@dataclass
class Test:
    id: str
    name: str
    status: str
    metrics: dict = field(default_factory=dict)
    tables: dict = field(default_factory=dict)   # {table_name: [row dict, ...]}
    notes: str = ""

    @property
    def ok(self) -> bool:
        return self.status in GOOD

    @property
    def bad(self) -> bool:
        return self.status in BAD


@dataclass
class Suite:
    name: str
    tests: list[Test]

    @property
    def label(self) -> str:
        return SUITE_LABEL.get(self.name, self.name.title())

    @property
    def counts(self) -> dict:
        c = {"good": 0, "bad": 0, "other": 0}
        for t in self.tests:
            c["good" if t.ok else "bad" if t.bad else "other"] += 1
        return c


@dataclass
class Campaign:
    key: str                      # directory name (unique id used in URLs)
    campaign_id: str              # the campaign field inside results.json
    started: str
    sut: dict
    suites: list[Suite]
    kpis: dict = field(default_factory=dict)
    n_commands: int = 0
    verdict: dict = field(default_factory=dict)   # CNTC graded verdict block (if present)
    status: str = "complete"      # "running" while the campaign is executing (live), else "complete"
    running_suite: str = ""       # the suite currently executing (when status == "running")

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    # --- convenience accessors ------------------------------------------------
    @property
    def mode(self) -> str:
        return (self.sut.get("mode") or "?").lower()

    @property
    def upf(self) -> str:
        return self.sut.get("upf", "UPF")

    @property
    def date(self) -> str:
        return (self.started or "").replace("T", " ")[:19]

    @property
    def workers(self) -> int | None:
        """Data-plane worker count — from the SUT if captured, else parsed from a 'W<n>'
        campaign name (e.g. W4-ALL), else None."""
        w = self.sut.get("workers")
        if w:
            try: return int(w)
            except (TypeError, ValueError): pass
        m = re.search(r"\bW(\d+)\b", self.key)
        return int(m.group(1)) if m else None

    @property
    def group(self) -> str:
        """'verified' (credible result, shown by default) or 'experimental' (dev/debug)."""
        if not self.totals["tests"] or self.mode in ("?", "unknown", ""):
            return "experimental"
        return "verified" if _VERIFIED_RE.match(self.campaign_id or self.key) else "experimental"

    @property
    def suite_names(self) -> list[str]:
        return [s.name for s in self.suites]

    def suite(self, name: str) -> Suite | None:
        return next((s for s in self.suites if s.name == name), None)

    @property
    def totals(self) -> dict:
        t = {"good": 0, "bad": 0, "other": 0, "tests": 0}
        for s in self.suites:
            c = s.counts
            t["good"] += c["good"]; t["bad"] += c["bad"]; t["other"] += c["other"]
            t["tests"] += len(s.tests)
        return t


def _parse(path: Path) -> Campaign | None:
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return None
    suites = []
    for s in raw.get("suites", []) or []:
        tests = [Test(id=t.get("id", "?"), name=t.get("name", ""),
                      status=(t.get("status") or "").lower(),
                      metrics=t.get("metrics") or {}, tables=t.get("tables") or {},
                      notes=t.get("notes") or "")
                 for t in s.get("tests", []) or []]
        suites.append(Suite(name=s.get("suite", "?"), tests=tests))
    # stable suite order (known suites first, in SUITE_ORDER; unknown after)
    suites.sort(key=lambda s: (SUITE_ORDER.index(s.name) if s.name in SUITE_ORDER else 99,
                               s.name))
    return Campaign(
        key=path.parent.name,
        campaign_id=raw.get("campaign", path.parent.name),
        started=raw.get("started", ""),
        sut=raw.get("sut") or {},
        suites=suites,
        kpis=raw.get("kpis") or {},
        n_commands=len(raw.get("commands") or []),
        verdict=raw.get("verdict") or {},
        status=raw.get("status", "complete"),
        running_suite=raw.get("running_suite") or "",
    )


def load_campaigns() -> list[Campaign]:
    """All parseable campaigns, newest first. Empty (no tests) campaigns are kept but sort
    last so debug stubs don't crowd the top."""
    out = []
    if CAMPAIGNS_DIR.is_dir():
        for d in CAMPAIGNS_DIR.iterdir():
            rj = d / "results.json"
            if rj.is_file():
                c = _parse(rj)
                if c is not None:
                    out.append(c)
    out.sort(key=lambda c: (c.totals["tests"] > 0, c.started), reverse=True)
    return out


def get_campaign(key: str) -> Campaign | None:
    rj = CAMPAIGNS_DIR / key / "results.json"
    return _parse(rj) if rj.is_file() else None


def campaigns_by_mode() -> dict[str, list[Campaign]]:
    """Group non-empty campaigns by dataplane mode (for the compare view)."""
    groups: dict[str, list[Campaign]] = {}
    for c in load_campaigns():
        if c.totals["tests"]:
            groups.setdefault(c.mode, []).append(c)
    return groups
