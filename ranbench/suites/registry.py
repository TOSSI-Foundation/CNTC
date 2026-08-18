"""Build the list of test cases for one product class's suite.

Each class has a package under ``ranbench/suites/<target>/``; every module in it exposes a
``TESTS`` list of real :class:`RanTestCase` classes. The registry merges those with
:class:`StubCase` fillers for any requirement in that class's catalog
(``cntc/standards/<target>-conformance.yaml``) that has no real case yet, so the suite always
covers the full published standard, and an unimplemented requirement is scored 'na', never
dropped.
"""
from __future__ import annotations

import importlib
import pkgutil

from ranbench.suites.base import RanTestCase, StubCase

# What each product class's suite needs wired (used by the runner to build the RunContext):
#   ue: a UE must be attached to produce the procedure under test
#   core: a live AMF/UPF peer is required
#   observer: the wire/pcap decode is required
TARGET_REQUIRES = {
    "cucp": {"ue": True, "core": True, "observer": True},
    "cuup": {"ue": True, "core": True, "observer": True},
    "du":   {"ue": True, "core": True, "observer": True},
}


def _real_cases(target: str) -> dict[str, RanTestCase]:
    """Discover real RanTestCase classes in ranbench.suites.<target>, keyed by test id."""
    cases: dict[str, RanTestCase] = {}
    try:
        pkg = importlib.import_module(f"ranbench.suites.{target}")
    except ModuleNotFoundError:
        return cases
    if not hasattr(pkg, "__path__"):
        return cases
    for mod_info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if mod_info.name in ("base", "registry"):
            continue
        mod = importlib.import_module(f"ranbench.suites.{target}.{mod_info.name}")
        for cls in getattr(mod, "TESTS", []):
            inst = cls()
            cases[inst.id] = inst
    return cases


def build_suite(target: str) -> list[RanTestCase]:
    """Real cases where implemented, StubCase for every other requirement in the catalog,
    listed in catalog order so the scorecard is deterministic and complete."""
    from cntc.standards import load_catalog
    catalog = load_catalog(f"{target}-conformance")
    real = _real_cases(target)
    suite: list[RanTestCase] = []
    for t in catalog.get("tests", []):
        tid = t["id"]
        suite.append(real.get(tid) or StubCase(tid, t.get("name", tid), target))
    return suite
