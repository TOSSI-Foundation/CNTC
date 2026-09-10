"""OAI nr-UE against an L1/L2 split gNB: bring the chain up in order, attach, decode nFAPI.

The UE itself is the same binary the CU/DU rigs use and every attach milestone is parsed the
same way, so this reuses that driver and overrides only the four things this deployment does
differently:

* **bring-up order**, which is CU, VNF, PNF, L2 rather than CU-CP, CU-UP, DU, and where the
  ordering is enforced by the products rather than merely preferred (see the adapter docstring)
* **teardown order**, the reverse, so the shared memory region is released before the next run
* **the decode**, which reads nFAPI off P5 and P7 with the dedicated observer instead of asking
  tshark, because tshark's nfapi dissector names NR messages after their LTE homonyms
* **the UE invocation**, which takes its subscriber credentials from an OAI config file rather
  than from the campaign, because that is how this rig is configured by hand

The attach itself is unchanged, and deliberately so: whether a UE reaches RRC_CONNECTED and gets
a PDU session is the same question regardless of how the gNB is split, and the milestones it is
judged on come from the UE's own log either way.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ranbench.config import expand_user_path
from ranbench.drivers.ue_oai_zmq import Driver as OaiUeDriver


class Driver(OaiUeDriver):
    name = "ue_oai_fapi"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        d = cfg.drivers
        # The rig keeps the SIM in an OAI config file. When one is given the credentials come
        # from there and the campaign's subscriber block is not turned into command-line
        # arguments, so the two cannot disagree about which SIM is under test.
        self.ue_conf = expand_user_path(d["ue_conf"]) if d.get("ue_conf") else None
        self.rfsim_addr = d.get("rfsim_server", "127.0.0.1")

    # --- the UE command -------------------------------------------------------
    def _argv(self) -> list[str]:
        if not self.ue_conf:
            return super()._argv()
        argv = [str(self.bin), "-O", str(self.ue_conf),
                "--rfsim", "--rfsimulator.serveraddr", str(self.rfsim_addr),
                "-r", str(self.prb), "--numerology", str(self.numerology),
                "--band", str(self.band), "-C", str(self.freq_hz), "--ssb", str(self.ssb)]
        if self.uecap:
            argv += ["--uecap_file", str(self.uecap)]
        return argv

    def _kill_ue(self) -> None:
        """Stop the UE, escalating if it does not go.

        A UE wedged on the radio simulator's socket does not answer SIGTERM, and four of them
        accumulated across runs on this rig before this was added. They matter beyond tidiness:
        each one holds a connection to the simulator, and a stale one can keep the port busy for
        the next run's PNF.
        """
        super()._kill_ue()
        for _ in range(6):
            try:
                r = self._run("pgrep", "-x", "nr-uesoftmodem", timeout=10)
            except Exception:  # noqa: BLE001
                return
            if not (r.stdout or "").strip():
                return
            time.sleep(1)
        try:
            self._run("pkill", "-9", "-x", "nr-uesoftmodem", timeout=10)
        except Exception:  # noqa: BLE001
            pass

    # --- bring-up -------------------------------------------------------------
    def _bring_up(self, ran, obs: dict) -> None:
        """CU, then the southbound chain, then wait for the cell.

        The chain is asked for as one operation. Starting the VNF and the PNF separately from
        here would race the P5 handshake against the L2's single PARAM.request, and the adapter
        is the only thing that knows that constraint.
        """
        self._kill_ue()
        ran.reset()
        time.sleep(3)

        # Opened before the first product: the P5 handshake happens the moment the PNF connects
        # to the VNF, and a capture started afterwards misses the entire setup sequence that
        # PNF-P5-01 through PNF-P5-06 are judged on.
        ran.begin_evidence()

        obs["cu_started"] = ran.start("cu")
        # Waited for on the socket, never slept through. The L2 makes exactly one F1-C
        # connection attempt and exits when it is refused, and the CU binds that listener some
        # time after it has finished NG Setup. A fixed delay let the L2 be refused on this rig,
        # after which the PHY ran with no cell admitted and the UE had nothing to synchronise
        # to, which read as a PNF fault rather than a bring-up race.
        obs["cu_f1c_listening"] = (ran.wait_for_cu_f1c(60)
                                   if hasattr(ran, "wait_for_cu_f1c") else None)
        if obs["cu_f1c_listening"] is False:
            print("[ranbench] warning: the CU never accepted on F1-C, so the L2 will be "
                  "refused and no cell will be admitted.")

        obs["chain_started"] = ran.start("pnf")      # brings up VNF -> PNF -> L2 in order
        if not obs["chain_started"]:
            print("[ranbench] warning: the nFAPI chain did not assemble. The attach is "
                  "attempted anyway and the capture will show how far the setup got.")
        # A rig that cannot produce a stimulus must say so, loudly, before anything is
        # measured. Otherwise the resulting silence is recorded against the product.
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
        """UE first, then L2, PNF, VNF, CU, then close the captures.

        The order is the reverse of bring-up for a reason beyond tidiness: an L2 left running
        against a departed PNF holds the shared memory region open, and the next run's VNF then
        cannot create it. Captures close last so the P5 STOP exchange is inside the window,
        which is the evidence PNF-P5-08 is judged on.
        """
        self._kill_ue()
        time.sleep(float(self.release_settle_s))
        try:
            ran.stop("pnf")            # takes the L2 down with it
            time.sleep(3)
            ran.stop("vnf")
            time.sleep(2)
            ran.stop("cu")
            time.sleep(2)
        except Exception:  # noqa: BLE001
            pass
        try:
            ran.end_evidence()
        except Exception:  # noqa: BLE001
            pass

    # --- evidence -------------------------------------------------------------
    def _decode(self, ran, observer, obs: dict) -> None:
        """Decode P5 and P7 into the facts the PNF and VNF suites assert on.

        ``observer`` is the tshark wire observer the runner wires in for every RAN campaign. It
        is not used here and must not be: it would name NR messages after LTE ones. The nFAPI
        observer is constructed against this deployment's ports instead.
        """
        from ranbench.observers.nfapi import NfapiObserver, PNF_TO_VNF, VNF_TO_PNF

        obs["pcaps"] = {}
        try:
            paths = ran.pcap_paths("pnf")
        except Exception:  # noqa: BLE001
            paths = {}
        obs["pcaps"] = paths
        if not paths:
            obs["decode_error"] = "no nFAPI capture was produced (was tcpdump available?)"
            return

        nf = NfapiObserver(self.cfg, self.store,
                           p5_vnf_port=getattr(ran, "p5_vnf", 50001),
                           p7_pnf_port=getattr(ran, "p7_pnf", 50010),
                           p7_vnf_port=getattr(ran, "p7_vnf", 50011))
        obs["nfapi"] = nf
        for iface, path in paths.items():
            obs[f"procs.{iface}.pnf"] = sorted(nf.procedures(path, PNF_TO_VNF) or [])
            obs[f"procs.{iface}.vnf"] = sorted(nf.procedures(path, VNF_TO_PNF) or [])
            obs[f"counts.{iface}"] = nf.counts(path)
            obs[f"transport.{iface}"] = sorted(nf.transport(path) or [])
            obs[f"epoch0.{iface}"] = nf.epoch0(path)
        if "p7" in paths:
            obs["slot"] = nf.slot_timing(paths["p7"])
            first = (nf.messages(paths["p7"]) or [None])[0]
            obs["p7_first_t"] = first.t if first else None
        self._archive_all(paths)

    def _archive_all(self, paths: dict[str, str]) -> None:
        """Copy the captures into the campaign, so the verdict stays auditable.

        The live files are overwritten the moment the next run opens its captures, and the
        robustness cases restart products inside this same campaign.
        """
        archive = Path(self.store.raw) / "pcap"
        for iface, path in paths.items():
            try:
                archive.mkdir(parents=True, exist_ok=True)
                src = Path(path)
                if src.exists() and src.stat().st_size > 0:
                    self._run("cp", str(src), str(archive / f"nfapi-{iface}.pcap"), timeout=60)
                    self._run("chmod", "644", str(archive / f"nfapi-{iface}.pcap"), timeout=10)
            except Exception:  # noqa: BLE001, archiving must never break a measurement
                pass
