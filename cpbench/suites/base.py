"""Test-case contract shared by every per-NF suite.

A suite is the set of test cases for one NF. The runner builds a :class:`RunContext` (the
wired-up core adapter / driver(s) / observer / store) and calls ``run`` on each case,
collecting a ``TestResult``. Adding a real test = drop an :class:`NfTestCase` subclass into
that NF's suite package and list it in the module's ``TESTS``. Any catalog test id without a
real case yet is filled by a :class:`StubCase` (graded 'na'), so the scorecard always lists
the full standard and never silently drops a requirement.
"""
from __future__ import annotations

import abc
import dataclasses
from typing import Any

from cntc_common.results import TestResult


@dataclasses.dataclass
class RunContext:
    cfg: Any                 # config.Campaign
    core: Any                # adapters.base.CoreAdapter
    driver: Any              # drivers.base.Driver | None  (N1/N2 driver)
    sbi: Any                 # drivers.sbi_client.Driver | None
    observer: Any            # observers.nas_ngap.NasNgapObserver | None
    store: Any               # cntc_common Store
    nf: str                  # the NF under test (amf/smf/nrf/ausf/udm)
    endpoint: str            # resolved host:port for this NF
    knobs: dict[str, Any]    # per-NF config knobs


class NfTestCase(abc.ABC):
    id: str = "XX-00"
    name: str = "unnamed"
    nf: str = ""

    @abc.abstractmethod
    def run(self, ctx: RunContext) -> TestResult:
        ...


class StubCase(NfTestCase):
    """Placeholder for a catalog requirement whose real test isn't implemented yet.

    Returns ``status='not_implemented'`` -> the CNTC verdict grades it 'na' (never pass),
    so an unbuilt requirement can never be mistaken for a satisfied one."""

    def __init__(self, tid: str, name: str, nf: str):
        self.id = tid
        self.name = name
        self.nf = nf

    def run(self, ctx: RunContext) -> TestResult:
        return TestResult(self.id, self.name, "not_implemented",
                          notes="stub, real test pending (see docs/PLAN-CONTROL-PLANE.md)")
