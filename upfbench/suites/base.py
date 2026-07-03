"""Test-case contract shared by all three suites.

A suite is a list of TestCase classes. The runner builds a RunContext (the wired-up
adapter / control / traffic / store) and calls ``run`` on each case, collecting a
TestResult. Adding a test = drop a TestCase subclass into the suite's module and list it
in that module's ``TESTS``.
"""
from __future__ import annotations

import abc
import dataclasses
from typing import Any

from upfbench.results import TestResult


@dataclasses.dataclass
class RunContext:
    cfg: Any                 # config.Campaign
    upf: Any                 # adapters.base.UPFAdapter
    control: Any             # control.base.ControlPlane | None
    traffic: Any             # traffic.base.TrafficGenerator | None
    store: Any               # results.Store
    knobs: dict[str, Any]    # the suite-specific config block


class TestCase(abc.ABC):
    id: str = "XX-00"
    name: str = "unnamed"

    @abc.abstractmethod
    def run(self, ctx: RunContext) -> TestResult:
        ...
