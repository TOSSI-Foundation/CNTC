"""Campaign configuration for the control-plane engine.

A cpbench campaign names the core distribution under test, which NF(s) to certify, the
driver(s) that exercise it, subscriber credentials for 5G-AKA, and the SUT facts that go into
the report. See ``configs/free5gc-cp.yaml`` for the canonical example.
"""
from __future__ import annotations

import dataclasses
import os
import pwd
from pathlib import Path
from typing import Any

import yaml

NFS = ("amf", "smf", "nrf", "ausf", "udm")
VALID_TARGETS = NFS + ("all",)


def expand_user_path(p: str | Path) -> Path:
    """Expand ``~`` the way the *invoking* user means it, even under ``sudo``.

    The run command is documented as ``sudo python3 -m cpbench.cli run …`` (tcpdump /
    pfcpsim / docker need root). Under sudo ``$HOME`` becomes ``/root``, so a plain
    ``Path("~/UERANSIM").expanduser()`` would look in ``/root`` and miss the user's build.
    When ``SUDO_USER`` is set we expand ``~`` against that user's real home instead, so the
    non-sudo ``doctor`` and the sudo ``run`` resolve the same path.
    """
    s = str(p)
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and (s == "~" or s.startswith("~/")):
        try:
            home = pwd.getpwnam(sudo_user).pw_dir
            return Path(home + s[1:])
        except KeyError:
            pass
    return Path(s).expanduser()


@dataclasses.dataclass
class CoreConfig:
    adapter: str                              # which adapters/<name>.py to load (free5gc/open5gs/oai)
    endpoints: dict[str, str] = dataclasses.field(default_factory=dict)  # nf -> "host:port"
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Campaign:
    campaign: str
    target_nf: str                            # amf|smf|nrf|ausf|udm|all
    sut: dict[str, Any]
    core: CoreConfig
    drivers: dict[str, str] = dataclasses.field(default_factory=dict)   # role -> driver name
    subscribers: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    knobs: dict[str, Any] = dataclasses.field(default_factory=dict)     # per-NF suite knobs
    baseline: str | None = None
    domain: str = "control-plane"

    @property
    def target_nfs(self) -> list[str]:
        """Expand the 'all' selector into the concrete NF list, in a stable order."""
        return list(NFS) if self.target_nf == "all" else [self.target_nf]

    @staticmethod
    def profile_for(nf: str) -> str:
        return f"{nf}-conformance"


def load(path: str | Path) -> Campaign:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    _require(raw, "campaign", str)
    target = raw.get("target_nf", "all")
    if target not in VALID_TARGETS:
        raise ValueError(f"target_nf must be one of {VALID_TARGETS}, got {target!r}")

    core_raw = raw.get("core") or {}
    if "adapter" not in core_raw:
        raise ValueError("config.core.adapter is required (e.g. 'free5gc')")
    known = {"adapter", "endpoints"}
    core = CoreConfig(
        adapter=core_raw["adapter"],
        endpoints=core_raw.get("endpoints", {}) or {},
        extra={k: v for k, v in core_raw.items() if k not in known},
    )

    return Campaign(
        campaign=raw["campaign"],
        target_nf=target,
        sut=raw.get("sut", {}) or {},
        core=core,
        drivers=raw.get("drivers", {}) or {},
        subscribers=raw.get("subscribers", []) or [],
        knobs=raw.get("knobs", {}) or {},
        baseline=raw.get("baseline"),
        domain=raw.get("domain", "control-plane"),
    )


def _require(d: dict, key: str, typ: type) -> None:
    if key not in d:
        raise ValueError(f"config.{key} is required")
    if not isinstance(d[key], typ):
        raise ValueError(f"config.{key} must be {typ.__name__}")
