"""Driver: how one interface of the RAN is exercised.

The RAN analog of ``cpbench.drivers``. Each driver owns its lifecycle (``setup``/``teardown``)
and exposes the procedures its interface supports as methods returning a structured
``{ok, ...}`` result the test cases assert on.
"""
from __future__ import annotations

import abc
import importlib


class Driver(abc.ABC):
    name: str = "base"

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    def setup(self) -> None:
        """Prepare the driver (build check, connect). Default: no-op."""

    def teardown(self) -> None:
        """Release the driver. Default: no-op."""

    @abc.abstractmethod
    def capabilities(self) -> set[str]:
        """The procedure names this driver can perform (e.g. {"attach", "data_path"}).
        A test whose procedure isn't in the driver's capabilities grades 'na', not fail."""


def load_driver(name: str, cfg, store) -> Driver:
    mod = importlib.import_module(f"ranbench.drivers.{name}")
    cls = getattr(mod, "Driver")
    return cls(cfg, store)
