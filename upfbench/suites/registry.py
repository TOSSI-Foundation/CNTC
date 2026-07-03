"""Discover the TestCase classes that make up each suite.

Each suite is a package of modules; every module exposes a ``TESTS`` list. The registry
imports them in a stable order so the report lists tests deterministically.
"""
from __future__ import annotations

import importlib
import pkgutil

from upfbench.suites.base import TestCase

# Which control/traffic plugins each suite needs by default (used by the runner to wire
# the RunContext). Suite 1 uses the white-box fast path; 2 & 3 use standardized PFCP.
SUITE_REQUIRES = {
    "performance": {"control": "pybess", "traffic": "testpmd"},
    "load": {"control": "pfcpsim", "traffic": "trex"},
    "pfcp": {"control": "pfcpsim", "traffic": None},
    "n3neg": {"control": "pfcpsim", "traffic": "trex"},
}


def build_suite(name: str) -> list[TestCase]:
    pkg = importlib.import_module(f"upfbench.suites.{name}")
    cases: list[TestCase] = []
    for mod_info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if mod_info.name in ("base", "registry"):
            continue
        mod = importlib.import_module(f"upfbench.suites.{name}.{mod_info.name}")
        for cls in getattr(mod, "TESTS", []):
            cases.append(cls())
    return cases
