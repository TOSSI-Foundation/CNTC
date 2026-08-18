"""Driver: how the control plane is exercised over an interface.

Drivers are the control-plane analog of ``upfbench.control`` / ``upfbench.traffic``:
  * ``ueransim`` / ``gnbsim`` / ``packetrusher``, drive N1/N2 (NAS + NGAP) through the AMF.
  * ``sbi_client``, drive SBI (HTTP/2 + TLS + OAuth2) directly.

Each owns its lifecycle (``setup``/``teardown``) and exposes the procedures its interface
supports as methods returning a structured ``{ok, ...}`` result the NF test cases assert on.
"""
from __future__ import annotations

import abc
import importlib
from typing import Any


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
        """The procedure names this driver can perform (e.g. {"register","authenticate"}).
        A test whose procedure isn't in the driver's capabilities grades 'na', not fail."""


def load_driver(name: str, cfg, store) -> Driver:
    mod = importlib.import_module(f"cpbench.drivers.{name}")
    cls = getattr(mod, "Driver")
    return cls(cfg, store)
