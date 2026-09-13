"""Pure-OAI L1/L2 split as the RAN under test: OAI PNF and OAI VNF across nFAPI.

This is the sibling of :mod:`ranbench.adapters.fapi_split`. That adapter certifies the same two
SCF product classes, but in a deployment where the VNF role is filled by a translator (xFAPI) in
front of a separate OCUDU L2, with a CU behind it: four processes, DPDK shared memory between the
VNF and the L2, and F1 to the CU. Here both ends of nFAPI are OAI ``nr-softmodem``:

    UE --rfsim--> PNF (OAI L1) <--nFAPI P5/P7--> VNF (OAI L2+L3) --N2/N3--> 5G core
                  ^^^ certified                  ^^^ certified

The certified pair, the catalogs, the observer and the suites are identical to the xFAPI stack,
because all of them judge the nFAPI interface and the interface is the same wire either way. What
differs is only the bring-up, and it is *simpler*:

* **Two processes, not four.** The OAI VNF is a full gNB: it carries MAC, RLC, PDCP and RRC and
  terminates N2/N3 to the core itself, so there is no separate L2 and no CU, and no F1 on any
  wire. Nothing here starts or waits on either.
* **No DPDK.** The OAI VNF is not a DPDK primary and owns no hugepage region, so the stale-region
  cleanup the xFAPI VNF needs does not apply.
* **Order is VNF then PNF.** The VNF is the P5 SCTP listener (SCF225: the VNF listens, the PNF
  dials it), so it must be bound before the PNF connects. The ``--nfapi VNF`` / ``--nfapi PNF``
  switch selects each role; without it ``nr-softmodem`` starts monolithic and the PNF config is
  rejected outright.

Restarting the PNF still means restarting the VNF: the VNF does not repeat the P5 handshake, so
a PNF that comes back finds a VNF that will never configure it again. ``start`` therefore rebuilds
the pair, exactly as the xFAPI adapter rebuilds its chain, which is why the robustness cases that
restart a product get a working stack rather than a half-attached one.

Everything else, the capture wiring, the nFAPI port map, readiness on the actual socket, the
rfsim-port guard, per-component version, ``node_endpoint`` and ``pcap_paths``, is inherited from
:class:`ranbench.adapters.fapi_split.Adapter` unchanged.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from ranbench.adapters.fapi_split import Adapter as FapiSplitAdapter

# The two certified product classes. There are no peers in this stack: the VNF is the gNB.
TARGETS = ("pnf", "vnf")
# Bring-up order. The VNF is the P5 listener and must be bound before the PNF dials it.
ORDER = ("vnf", "pnf")


class Adapter(FapiSplitAdapter):
    name = "fapi_oai"

    # --- process identity -----------------------------------------------------
    def _conf_name(self, key: str) -> str:
        """The .conf basename this product was started with, which is the only thing that tells
        the VNF and the PNF apart: both are ``nr-softmodem``."""
        for a in (self.procs.get(key) or {}).get("args", []):
            if str(a).endswith(".conf"):
                return Path(a).name
        return ""

    def _pid(self, key: str) -> str:
        """The pid of one product, matched on its config file.

        The VNF and the PNF are the same executable in this stack, so ``pgrep -x nr-softmodem``
        returns whichever the kernel lists first and silently answers about the wrong product.
        Matching on the config the process was started with is the only reliable discriminator,
        the same way the OAI CU/DU adapter tells the CU-CP and the DU apart.
        """
        conf, exe = self._conf_name(key), self._procname(key)
        if not conf:
            return super()._pid(key)
        try:
            r = subprocess.run(["pgrep", "-a", "-f", conf], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return ""
        for line in (r.stdout or "").splitlines():
            pid, _, cmd = line.partition(" ")
            if conf in cmd and exe in cmd and "pgrep" not in cmd:
                return pid.strip()
        return ""

    def _kill(self, key: str, graceful: bool = True) -> None:
        """Kill one product by pid, so stopping the PNF does not also take the VNF.

        The inherited ``pkill -x nr-softmodem`` would match both, because they share a name.
        """
        pid = self._pid(key)
        if not pid:
            return
        self._run("kill", "-INT" if graceful else "-KILL", pid, timeout=10)

    # --- lifecycle ------------------------------------------------------------
    def reset(self) -> None:
        """Stop both products. No DPDK region to clear: the OAI VNF owns none."""
        for key in ("pnf", "vnf"):
            self._kill(key, graceful=False)
        for key in ("pnf", "vnf"):
            self._await_gone(key, 10)

    def start(self, target: str) -> bool:
        """Start the pair. Restarting either rebuilds both, because the VNF does not repeat the
        P5 handshake, so a PNF restarted alone comes back to a VNF that will never configure it."""
        if target not in TARGETS:
            return False
        return self._start_chain()

    def _start_chain(self) -> bool:
        """VNF, then PNF, each waited for on the socket that proves it is ready.

        The VNF is launched first and its P5 SCTP listener waited for, because the PNF dials that
        port on startup and exits if it is refused. The PNF is launched second, and the run only
        proceeds once P5 has actually associated (not merely that the VNF is listening) and P7 has
        begun, so a stack that assembled halfway is caught here rather than measured downstream.
        """
        for key in ("pnf", "vnf"):
            self._kill(key, graceful=False)
        for key in ("pnf", "vnf"):
            self._await_gone(key, 10)

        if not self._launch("vnf"):
            return False
        # The VNF listens on P5 (SCTP). Wait for the listener before the PNF dials it.
        if not self.wait_for_listen(self.p5_vnf, 30):
            return False
        # Checked before the PNF starts: afterwards a held rfsim port is indistinguishable from a
        # PHY that simply is not transmitting, and the run would blame the PHY for it.
        holder = self.rfsim_conflict()
        if holder:
            self._rig_faults.append(
                f"the radio simulator port {self.rfsim_port} was already held by {holder}, so "
                f"this PNF cannot bind it and will transmit void samples. No UE can synchronise "
                f"and nothing measured against it describes the PHY.")
        if not self._launch("pnf"):
            return False
        time.sleep(6)
        # The product's own account, used only to detect a broken rig, never to grade anything.
        console = self._console("pnf")
        try:
            if console.exists() and "Could not start the RF device" in console.read_text(
                    errors="ignore"):
                self._rig_faults.append(
                    "the PNF could not start its radio device and is generating void samples, "
                    "so it is transmitting nothing for a UE to find.")
        except OSError:
            pass
        # P5 must associate, not merely be listened for: the VNF logs that it is listening long
        # before the PNF connects, so the association is the only honest signal both ends met.
        if not self.wait_for_p5(45):
            return False
        # P7 begins once the PHY instance is RUNNING.
        self.wait_for_udp(self.p7_pnf, 30)
        time.sleep(self._settle)
        return self._alive("pnf") and self._alive("vnf")

    def stop(self, target: str, graceful: bool = True) -> None:
        """Stop one product. Nothing else depends on it in this stack, so only it is stopped;
        the driver controls the order across the two."""
        if target not in TARGETS:
            return
        self._kill(target, graceful)

    # --- introspection --------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        facts: dict[str, Any] = {"ran": "L1/L2 split over nFAPI (pure OAI)",
                                 "adapter": self.name, "mode": "fapi-oai (OAI PNF + OAI VNF)"}
        built = [k for k in ORDER if self.procs.get(k) and Path(self.procs[k]["bin"]).exists()]
        up = [k for k in ORDER if self._alive(k)]
        facts["nodes_built"] = ", ".join(built) if built else "(none found)"
        facts["nodes_up"] = ", ".join(up) if up else "(none running)"
        facts["nfapi"] = (f"P5 sctp {self.p5_pnf}<->{self.p5_vnf}, "
                          f"P7 udp {self.p7_pnf}<->{self.p7_vnf}")
        facts["pnf"] = self._procname("pnf")
        facts["vnf"] = self._procname("vnf")
        versions = {k: self._version(k) for k in ORDER}
        named = [f"{k}={v}" for k, v in versions.items() if v]
        if named:
            facts["ran_release"] = ", ".join(named)
        return facts

    def log_paths(self, target: str) -> dict[str, str]:
        """Console logs for the two products, for diagnosis only, never a verdict."""
        out: dict[str, str] = {}
        for key in ORDER:
            p = self._console(key)
            if p.exists():
                out[key] = str(p)
        return out
