"""Test-case contract shared by every per-product-class suite.

A suite is the set of test cases for one split-gNB product class. The runner builds a
:class:`RunContext` (the wired-up adapter / driver(s) / observer / store) and calls ``run`` on
each case, collecting a ``TestResult``. Adding a real test = drop a :class:`RanTestCase`
subclass into that class's suite package and list it in the module's ``TESTS``. Any catalog
test id without a real case yet is filled by a :class:`StubCase` (graded 'na'), so the scorecard
always lists the full standard and never silently drops a requirement.
"""
from __future__ import annotations

import abc
import dataclasses
from typing import Any

from cntc_common.results import TestResult


@dataclasses.dataclass
class RunContext:
    cfg: Any                 # config.Campaign
    ran: Any                 # adapters.base.RanAdapter, the stack under test
    core: Any                # the 5G core peer (AMF/UPF), or None
    ue: Any                  # the UE driver (Uu stimulus), or None
    observer: Any            # wire/pcap observer, or None
    store: Any               # cntc_common Store
    target: str              # the product class under test (cucp/cuup/du)
    endpoint: str            # resolved host:port for this target
    knobs: dict[str, Any]    # per-target config knobs


class RanTestCase(abc.ABC):
    id: str = "XX-00"
    name: str = "unnamed"
    target: str = ""

    @abc.abstractmethod
    def run(self, ctx: RunContext) -> TestResult:
        ...


class StubCase(RanTestCase):
    """Placeholder for a catalog requirement whose real test isn't implemented yet.

    Returns ``status='not_implemented'`` -> the CNTC verdict grades it 'na' (never pass), so an
    unbuilt requirement can never be mistaken for a satisfied one."""

    def __init__(self, tid: str, name: str, target: str):
        self.id = tid
        self.name = name
        self.target = target

    def run(self, ctx: RunContext) -> TestResult:
        return TestResult(self.id, self.name, "not_implemented",
                          notes="stub, real test pending (see ~/cntc-ran-docs/RANBENCH-PLAN.md)")
