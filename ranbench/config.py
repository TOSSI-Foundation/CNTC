"""Campaign configuration for the RAN engine.

A ranbench campaign names the RAN stack under test (the thing being certified), the 5G core
that peers with it over N2/N3 (bring-your-own), the UE driver that stimulates it, subscriber
credentials for 5G-AKA, and the SUT facts that go into the report. See
``configs/ocudu-ran.yaml`` for the canonical example.

Targets are the 3GPP TS 33.523 **product classes** of a split gNB, not "the gNB": each is a
separately addressable product with its own catalog and its own certificate.
"""
from __future__ import annotations

import dataclasses
import os
import pwd
from pathlib import Path
from typing import Any

import yaml

# Two different cuts through a gNB, each with its own product classes. They are kept apart
# because "all" must mean "every class of THIS deployment": a CU/DU rig has no PNF, and asking
# a FAPI-split rig for a CU-UP suite would measure a product that is not there.
#
#   cu-du   the 3GPP TS 33.523 split: CU-CP / CU-UP / DU over F1 and E1
#   fapi    the SCF split 6: VNF / PNF over nFAPI P5 and P7
CUDU_TARGETS = ("du", "cucp", "cuup")     # pipeline order (DU -> CU-CP -> CU-UP)
FAPI_TARGETS = ("pnf", "vnf")

# Kept under the old name: every existing config and caller means the CU/DU set by "TARGETS".
TARGETS = CUDU_TARGETS
ALL_TARGETS = CUDU_TARGETS + FAPI_TARGETS
VALID_TARGETS = ALL_TARGETS + ("all",)

# Which family a campaign belongs to, selected by `split:` in the campaign file.
SPLITS = {"cu-du": CUDU_TARGETS, "fapi": FAPI_TARGETS}


def expand_user_path(p: str | Path) -> Path:
    """Expand ``~`` the way the *invoking* user means it, even under ``sudo``.

    A run is documented as ``sudo python3 -m ranbench.cli run …`` (tcpdump and the RAN
    processes need root). Under sudo ``$HOME`` becomes ``/root``, so a plain
    ``Path("~/ocudu").expanduser()`` would miss the user's build. When ``SUDO_USER`` is set we
    expand against that user's real home, so the non-sudo ``doctor`` and the sudo ``run``
    resolve to the same path.
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
class RanConfig:
    """The RAN stack under test, the subject of the certificate."""
    adapter: str                              # which adapters/<name>.py to load (e.g. ocudu)
    endpoints: dict[str, str] = dataclasses.field(default_factory=dict)  # target -> "host:port"
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class CoreConfig:
    """The 5G core the RAN peers with, a *peer*, not the subject. Bring-your-own."""
    adapter: str = ""                         # free5gc_k8s | sdcore | open5gs | ""
    endpoints: dict[str, str] = dataclasses.field(default_factory=dict)
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Campaign:
    campaign: str
    target: str                               # du|cucp|cuup|all
    sut: dict[str, Any]
    ran: RanConfig
    core: CoreConfig
    drivers: dict[str, Any] = dataclasses.field(default_factory=dict)   # role -> driver name/opts
    subscribers: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    knobs: dict[str, Any] = dataclasses.field(default_factory=dict)     # per-target suite knobs
    baseline: str | None = None
    domain: str = "ran"
    split: str = "cu-du"                      # cu-du | fapi, see SPLITS above

    @property
    def targets(self) -> list[str]:
        """Expand the 'all' selector into the concrete target list, in a stable order.

        'all' means every product class of *this* split, not every class ranbench knows. A
        campaign against a FAPI-split rig that expanded to the CU/DU classes would spend its
        time measuring products the deployment does not contain and record 'na' for all of them.
        """
        if self.target != "all":
            return [self.target]
        return list(SPLITS.get(self.split, CUDU_TARGETS))

    @staticmethod
    def profile_for(target: str) -> str:
        return f"{target}-conformance"


def load(path: str | Path) -> Campaign:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    _require(raw, "campaign", str)
    target = raw.get("target", "all")
    if target not in VALID_TARGETS:
        raise ValueError(f"target must be one of {VALID_TARGETS}, got {target!r}")
    split = raw.get("split", "cu-du")
    if split not in SPLITS:
        raise ValueError(f"split must be one of {sorted(SPLITS)}, got {split!r}")
    if target != "all" and target not in SPLITS[split]:
        raise ValueError(f"target {target!r} does not belong to the {split!r} split "
                         f"(its classes are {SPLITS[split]})")

    ran_raw = raw.get("ran") or {}
    if "adapter" not in ran_raw:
        raise ValueError("config.ran.adapter is required (e.g. 'ocudu')")
    known = {"adapter", "endpoints"}
    ran = RanConfig(
        adapter=ran_raw["adapter"],
        endpoints=ran_raw.get("endpoints", {}) or {},
        extra={k: v for k, v in ran_raw.items() if k not in known},
    )

    core_raw = raw.get("core") or {}
    core = CoreConfig(
        adapter=core_raw.get("adapter", ""),
        endpoints=core_raw.get("endpoints", {}) or {},
        extra={k: v for k, v in core_raw.items() if k not in known},
    )

    return Campaign(
        campaign=raw["campaign"],
        target=target,
        sut=raw.get("sut", {}) or {},
        ran=ran,
        core=core,
        drivers=raw.get("drivers", {}) or {},
        subscribers=raw.get("subscribers", []) or [],
        knobs=raw.get("knobs", {}) or {},
        baseline=raw.get("baseline"),
        domain=raw.get("domain", "ran"),
        split=split,
    )


def _require(d: dict, key: str, typ: type) -> None:
    if key not in d:
        raise ValueError(f"config.{key} is required")
    if not isinstance(d[key], typ):
        raise ValueError(f"config.{key} must be {typ.__name__}")
