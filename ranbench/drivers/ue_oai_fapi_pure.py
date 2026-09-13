"""OAI nr-UE against a pure-OAI L1/L2 split (OAI PNF + OAI VNF).

The sibling of :mod:`ranbench.drivers.ue_oai_fapi`. That driver brings up the xFAPI stack, whose
chain is CU, VNF, PNF, L2. This one brings up the two-process OAI stack, whose only order is VNF
then PNF, and where the VNF terminates N2/N3 to the core itself so there is no CU to start and no
F1 to wait on.

Everything that judges the interface is inherited: ``_argv`` (UE from the campaign config over
rfsim), ``_attach`` and its milestones, ``_kill_ue``, and ``_decode`` (which reads P5 and P7 with
the dedicated nFAPI observer). Only the bring-up and the teardown are this stack's own.

The AMF is reset by the runner before the campaign, from ``core.reset_ue_contexts`` on the
free5GC core adapter, so this driver does not touch the core: the VNF issues NG Setup on startup
and by then the AMF is up with no stale UE context.
"""
from __future__ import annotations

import time

from ranbench.drivers.ue_oai_fapi import Driver as FapiUeDriver


class Driver(FapiUeDriver):
    name = "ue_oai_fapi_pure"

    def _bring_up(self, ran, obs: dict) -> None:
        """Reset, open the capture, then start the pair as one operation.

        The capture is opened before the first product because the P5 handshake happens the moment
        the PNF connects to the VNF, and a capture started afterwards misses the whole setup
        sequence the P5 requirements are judged on. ``start('pnf')`` brings up VNF then PNF in the
        adapter, which is the only thing that knows the VNF must be the P5 listener first.
        """
        self._kill_ue()
        ran.reset()
        time.sleep(3)

        ran.begin_evidence()

        obs["chain_started"] = ran.start("pnf")      # VNF then PNF, in the adapter
        if not obs["chain_started"]:
            print("[ranbench] warning: the nFAPI pair did not assemble. The attach is attempted "
                  "anyway and the capture will show how far the setup got.")
        # A rig that cannot produce a stimulus must say so before anything is measured, or the
        # resulting silence is recorded against the product.
        faults = ran.rig_faults() if hasattr(ran, "rig_faults") else []
        if faults:
            obs["rig_faults"] = faults
            print("\n[ranbench] RIG FAULT, this run cannot measure the RAN:")
            for f in faults:
                print(f"[ranbench]   {f}")
            print()
        obs["p5_associated"] = ran.wait_for_p5(20) if hasattr(ran, "wait_for_p5") else None
        obs["cell_active"] = ran.wait_for_cell(60)
        if not obs["cell_active"]:
            print("[ranbench] warning: P7 never started, so the PHY is not serving a cell; "
                  "the UE is launched anyway and the attach will show it.")

    def _stop_all(self, ran) -> None:
        """UE first, then PNF, then VNF, then close the captures.

        Captures close last so the P5 STOP exchange is inside the window, which is the evidence
        PNF-P5-08 and VNF-P5-06 are judged on. PNF before VNF so the PHY stops requesting slots
        from a VNF that is going away.
        """
        self._kill_ue()
        time.sleep(float(self.release_settle_s))
        try:
            ran.stop("pnf")
            time.sleep(3)
            ran.stop("vnf")
            time.sleep(2)
        except Exception:  # noqa: BLE001
            pass
        try:
            ran.end_evidence()
        except Exception:  # noqa: BLE001
            pass
