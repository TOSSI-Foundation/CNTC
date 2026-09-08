"""Build the list of test cases for one NF's suite.

Each NF has a package under ``cpbench/suites/<nf>/``; every module in it exposes a ``TESTS``
list of real :class:`NfTestCase` classes. The registry merges those with :class:`StubCase`
fillers for any requirement in that NF's catalog (``cntc/standards/<nf>-conformance.yaml``)
that has no real case yet, so the suite always covers the full published standard, and an
unimplemented requirement is scored 'na', never dropped.

Which driver(s) each NF's suite needs (used by the runner to wire the RunContext):
"""
from __future__ import annotations

import importlib
import pkgutil

from cpbench.suites.base import NfTestCase, StubCase

# The N1/N2 (gnb) driver and whether the suite needs the SBI client / wire observer.
NF_REQUIRES = {
    "amf":  {"gnb": "ueransim", "sbi": True,  "observer": True},
    "smf":  {"gnb": "ueransim", "sbi": True,  "observer": False},
    "nrf":  {"gnb": None,       "sbi": True,  "observer": False},
    "ausf": {"gnb": "ueransim", "sbi": True,  "observer": False},
    "udm":  {"gnb": "ueransim", "sbi": True,  "observer": False},
    # UDR is the system of record behind the UDM, PCF and NEF. Its data types are
    # individually addressable over Nudr, so it needs no UE and no gNB: the SBI alone.
    "udr":  {"gnb": None,       "sbi": True,  "observer": False},
    # PCF policy associations are created and released over Npcf directly, so no UE either.
    "pcf":  {"gnb": None,       "sbi": True,  "observer": False},
}


def _real_cases(nf: str) -> dict[str, NfTestCase]:
    """Discover real NfTestCase classes in cpbench.suites.<nf>, keyed by test id."""
    cases: dict[str, NfTestCase] = {}
    try:
        pkg = importlib.import_module(f"cpbench.suites.{nf}")
    except ModuleNotFoundError:
        return cases
    if not hasattr(pkg, "__path__"):
        return cases
    for mod_info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if mod_info.name in ("base", "registry"):
            continue
        mod = importlib.import_module(f"cpbench.suites.{nf}.{mod_info.name}")
        for cls in getattr(mod, "TESTS", []):
            inst = cls()
            cases[inst.id] = inst
    return cases


def build_suite(nf: str) -> list[NfTestCase]:
    """Real cases where implemented, StubCase for every other requirement in the catalog,
    listed in catalog order so the scorecard is deterministic and complete."""
    from cntc.standards import load_catalog
    catalog = load_catalog(f"{nf}-conformance")
    real = _real_cases(nf)
    suite: list[NfTestCase] = []
    for t in catalog.get("tests", []):
        tid = t["id"]
        suite.append(real.get(tid) or StubCase(tid, t.get("name", tid), nf))
    return suite
