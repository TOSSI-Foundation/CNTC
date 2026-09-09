#!/usr/bin/env python3
"""ranbench-configure.py: interactive wizard that builds a ranbench RAN campaign config.

The RAN is the fiddliest rig CNTC drives, because the UE and the O-DU have to agree on a set of
radio parameters exactly. Get one wrong and the symptom is silent: the UE finds the cell but
never decodes it, or attaches but never registers. So rather than ask you for those parameters,
this wizard **reads them out of the RAN's own configuration** and derives the UE side from them.

    ./scripts/ranbench-configure.py                          # interactive
    ./scripts/ranbench-configure.py --out configs/my-ran.yaml
    ./scripts/ranbench-configure.py --non-interactive        # accept every detected default

What it derives for you, from the O-DU's config:
  centre frequency   from dl_arfcn + band            (UE -C)
  PRB count          from channel_bandwidth + SCS    (UE -r)
  numerology         from common_scs                 (UE --numerology)
  ZMQ ports          from the DU's ru_sdr device_args, crossed over  (UE tx <-> DU rx)
  PLMN / TAC         from cell_cfg, cross-checked against the CU-CP's supported tracking areas

Anything it gets wrong you can edit in the generated YAML, the wizard is a convenience, the
config file is the source of truth. Verify with:  make ran-doctor CONFIG=<file>
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install -e .")

# NR-ARFCN -> Hz (TS 38.104 §5.4.2.1, table 5.4.2.1-1):
#     F = F_REF_Offs + dF_Global * (N - N_REF_Offs)
# The global raster has THREE ranges, and using the wrong one silently mistunes the UE by
# hundreds of MHz: band 78's ARFCN 632628 is 3489.42 MHz via the 3-24.25 GHz row, but 3163.14
# MHz if you wrongly apply the 5 kHz row. (dF_Global Hz, N_REF_Offs, F_REF_Offs Hz)
ARFCN_RANGES = [
    (600_000, 5_000, 0, 0),                                  # 0 - 3 GHz
    (2_016_667, 15_000, 600_000, 3_000_000_000),             # 3 - 24.25 GHz
    (3_279_165, 60_000, 2_016_667, 24_250_080_000),          # 24.25 - 100 GHz
]

# Transmission bandwidth configuration N_RB (TS 38.101-1 table 5.3.2-1), the subset we need.
PRB_TABLE = {
    15: {5: 25, 10: 52, 15: 79, 20: 106, 25: 133, 30: 160, 40: 216, 50: 270},
    30: {5: 11, 10: 24, 15: 38, 20: 51, 25: 65, 30: 78, 40: 106, 50: 133,
         60: 162, 80: 217, 90: 245, 100: 273},
    60: {10: 11, 15: 18, 20: 24, 25: 31, 30: 38, 40: 51, 50: 65, 60: 79,
         80: 107, 90: 121, 100: 135},
}


def arfcn_to_hz(arfcn: int) -> int:
    for upper, dfg, n_offs, f_offs in ARFCN_RANGES:
        if arfcn < upper:
            return int(f_offs + dfg * (arfcn - n_offs))
    raise ValueError(f"NR-ARFCN {arfcn} is out of range")


def scs_to_numerology(scs_khz: int) -> int:
    return {15: 0, 30: 1, 60: 2, 120: 3}.get(int(scs_khz), 1)


def prb_for(scs_khz: int, bw_mhz: int) -> int | None:
    return PRB_TABLE.get(int(scs_khz), {}).get(int(bw_mhz))


def ask(prompt: str, default: str, non_interactive: bool) -> str:
    if non_interactive:
        print(f"  {prompt}: {default}")
        return default
    got = input(f"  {prompt} [{default}]: ").strip()
    return got or default


def load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception as e:  # noqa: BLE001
        print(f"  !! could not read {path}: {e}")
        return {}


def zmq_ports(device_args: str) -> tuple[str, str]:
    """The DU's tx/rx ZMQ endpoints. The UE's are the mirror image: it transmits into the DU's
    rx and receives from the DU's tx, so getting these the same way round is a classic failure."""
    tx = re.search(r"tx_port=([^,]+)", device_args or "")
    rx = re.search(r"rx_port=([^,]+)", device_args or "")
    du_tx = tx.group(1) if tx else "tcp://127.0.0.1:4556"
    du_rx = rx.group(1) if rx else "tcp://127.0.0.1:4557"
    return du_rx, du_tx          # UE tx = DU rx ; UE rx = DU tx


def main() -> int:
    ap = argparse.ArgumentParser(description="build a ranbench RAN campaign config")
    ap.add_argument("--out", default="")
    ap.add_argument("--non-interactive", action="store_true")
    args = ap.parse_args()
    ni = args.non_interactive

    print("ranbench configure, RAN campaign config wizard\n")
    print("The RAN under test and the 5G core are bring-your-own; this only describes them.\n")

    print("RAN under test:")
    adapter = ask("adapter (ocudu | oai)", "ocudu", ni).strip().lower()
    # Each stack keeps its products and its configuration in its own shape, so the defaults
    # follow the choice. Offering one stack's paths for the other produced a config that looked
    # complete and could not start anything.
    if adapter == "oai":
        d_bin = "~/openairinterface5g/cmake_targets/ran_build/build"
        d_cucp, d_cuup, d_du = ("configs/oai/cucp.conf", "configs/oai/cuup.conf",
                                "configs/oai/du.conf")
    else:
        d_bin = "~/ocudu/build/apps"
        d_cucp, d_cuup, d_du = ("configs/ocudu/cu_cp.yml", "configs/ocudu/cu_up.yml",
                                "configs/ocudu/du_zmq.yml")
    bin_dir = ask("binary directory", d_bin, ni)
    cucp_cfg = ask("O-CU-CP config file", d_cucp, ni)
    cuup_cfg = ask("O-CU-UP config file", d_cuup, ni)
    du_cfg = ask("O-DU config file", d_du, ni)

    # --- derive the UE's radio parameters from the DU's own cell configuration -------
    # OAI configures itself with libconfig rather than YAML, and names the same quantities
    # differently, so the DU is read according to the stack it belongs to.
    ru: dict = {}
    ssb_sc = 0
    if adapter == "oai":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from ranbench.adapters import _libconfig as lc
        c = lc.read(Path(du_cfg).expanduser())
        g = lambda k, d: lc.first(c, k, str(d))
        arfcn = int(g("absoluteFrequencySSB", 641280))
        band = int(g("dl_frequencyBand", 78))
        scs = {0: 15, 1: 30, 2: 60, 3: 120}.get(int(g("dl_subcarrierSpacing", 1)), 30)
        prb_cfg = int(g("dl_carrierBandwidth", 106))
        mcc, mnc = g("mcc", 208), g("mnc", 93)
        plmn = f"{mcc}{mnc}"
        tac = int(g("tracking_area_code", 1))
        bw = 0                      # OAI states the PRB count directly, so no lookup is needed
    else:
        du = load_yaml(Path(du_cfg).expanduser())
        cell = du.get("cell_cfg") or {}
        ru = du.get("ru_sdr") or {}
        arfcn = int(cell.get("dl_arfcn", 632628))
        band = int(cell.get("band", 78))
        scs = int(cell.get("common_scs", 30))
        bw = int(cell.get("channel_bandwidth_MHz", 20))
        plmn = str(cell.get("plmn", "00101"))
        tac = cell.get("tac", 1)
        prb_cfg = None
    freq_hz = arfcn_to_hz(arfcn)
    numerology = scs_to_numerology(scs)
    prb = prb_cfg if prb_cfg else prb_for(scs, bw)
    ue_tx, ue_rx = zmq_ports(str(ru.get("device_args", "")))

    print(f"\nderived from {du_cfg}:")
    print(f"  band {band}, ARFCN {arfcn} -> {freq_hz/1e6:.2f} MHz")
    print(f"  {bw} MHz @ SCS {scs} kHz -> numerology {numerology}, "
          f"{prb if prb else '?'} PRB")
    print(f"  ZMQ: UE tx {ue_tx} / rx {ue_rx}  (mirror of the DU's)")
    print(f"  cell PLMN {plmn}, TAC {tac}")
    if adapter == "oai":
        # Deliberately asked rather than computed. It follows from absoluteFrequencySSB and
        # pointA, but getting it wrong is silent: the UE scans at the wrong offset, never finds
        # the cell, and the campaign reports a RAN that failed to serve one. The DU prints the
        # exact value on startup ("Command line parameters for OAI UE: ... --ssb N"), so the
        # right move is to read it from there rather than to guess it here.
        print("  the O-DU prints the UE's --ssb on startup; take it from that line")
        ssb_sc = int(ask("UE ssb start subcarrier (--ssb)", "516", ni))
    if prb is None:
        print("  !! could not derive the PRB count for that bandwidth/SCS, set drivers.prb by hand")
        prb = 51

    # cross-check the DU's cell against what the CU-CP tells the AMF; a mismatch here is
    # rejected at NG Setup or at registration, and the error is not obvious
    if adapter == "oai":
        cc = lc.read(Path(cucp_cfg).expanduser())
        cp_plmns = [f'{lc.first(cc, "mcc", "")}{lc.first(cc, "mnc", "")}'] if cc else []
        cp_tacs = [int(lc.first(cc, "tracking_area_code", 0) or 0)] if cc else []
        areas = bool(cc)
    else:
        cucp = load_yaml(Path(cucp_cfg).expanduser())
        areas = ((cucp.get("cu_cp") or {}).get("amf") or {}).get("supported_tracking_areas") or []
        cp_plmns = [p.get("plmn") for a in areas for p in (a.get("plmn_list") or [])] if areas else []
        cp_tacs = [a.get("tac") for a in areas] if areas else []
    if areas:
        if plmn not in [str(x) for x in cp_plmns if x is not None]:
            print(f"  !! the O-DU cell broadcasts PLMN {plmn} but the O-CU-CP advertises "
                  f"{cp_plmns}, the UE will be rejected")
        if tac not in cp_tacs:
            print(f"  !! the O-DU cell TAC {tac} is not in the CU-CP's {cp_tacs}")

    print("\n5G core (the N2/N3 peer, bring your own):")
    core_adapter = ask("core adapter", "free5gc_k8s", ni)
    namespace = ask("kubernetes namespace", "free5gc", ni)
    # Asked, and defaulted on, because leaving it out is not a neutral choice. A UE context
    # left in the AMF by the previous campaign makes it drop the next Registration Request
    # without an error anywhere: the UE reaches RRC_CONNECTED and stops, and the run records
    # "the attach never completed" across most of the catalog. Measured here, a config without
    # it graded 23 tests na for a reason that had nothing to do with the RAN. Say no only if
    # the core is shared or you manage its state yourself.
    print("  restarting the AMF before each run clears stale UE context;")
    print("  without it a second run silently fails to register")
    reset_amf = ask("reset the AMF before each campaign (yes/no)", "yes", ni).strip().lower()
    reset_amf = reset_amf not in ("n", "no", "false", "0")

    print("\nUE simulator (installed by scripts/bootstrap_ranbench.sh):")
    default_ue = "~/openairinterface5g/cmake_targets/ran_build/build/nr-uesoftmodem"
    ue_bin = ask("nr-uesoftmodem path", default_ue, ni)
    uecap = ask("UE capability file",
                "~/openairinterface5g/targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports1.xml", ni)

    print("\nSubscriber (must already be provisioned in your core):")
    supi = ask("SUPI", f"imsi-{plmn}0000000001", ni)
    ki = ask("Ki", "8baf473f2f8fd09487cccbd7097c6862", ni)
    opc = ask("OPc", "8e27b6af0e692e750f32667a3b14605d", ni)
    dnn = ask("DNN", "internet", ni)
    sst = int(ask("S-NSSAI SST", "1", ni))
    sd = ask("S-NSSAI SD (3-byte hex, blank for none)", "010203", ni)

    campaign = ask("\ncampaign id", f"{adapter.upper()}-RAN-001", ni)
    out = Path(args.out or ask("write config to", f"configs/{adapter}-ran.yaml", ni))

    cfg = {
        "domain": "ran",
        "campaign": campaign,
        "target": "all",
        "ran": {"adapter": adapter, "bin_dir": bin_dir, "sudo": True,
                "configs": {"cucp": cucp_cfg, "cuup": cuup_cfg, "du": du_cfg}},
        "core": {"adapter": core_adapter, "namespace": namespace,
                 "reset_amf": reset_amf},
        "drivers": {"ue": "ue_oai_zmq", "ue_bin": ue_bin, "uecap_file": uecap,
                    "prb": prb, "numerology": numerology, "band": band,
                    "centre_freq_hz": freq_hz, "ssb": ssb_sc,
                    # The virtual radio joining the UE to the DU: OCUDU exposes a ZeroMQ
                    # device, OAI serves its own rfsimulator from the DU.
                    **({"radio": "rfsim", "rfsim_server": "127.0.0.1"} if adapter == "oai"
                       else {"zmq_tx": ue_tx, "zmq_rx": ue_rx}),
                    "attach_timeout_s": 150, "ping_target": "8.8.8.8"},
        "subscribers": [{k: v for k, v in
                         (("supi", supi), ("ki", ki), ("opc", opc), ("plmn", plmn),
                          ("sst", sst), ("sd", sd), ("dnn", dnn)) if v != ""}],
        "sut": {"rig_class": "shared-vm", "cpu": "", "ran_release": ""},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# ranbench RAN campaign, generated by scripts/ranbench-configure.py\n"
        "#\n"
        "# The radio parameters below were DERIVED from the O-DU's own cell config; they must\n"
        "# stay in step with it. docs/RANBENCH-RIG.md lists what breaks when they don't.\n"
        "#\n"
        f"#   make ran-doctor  CONFIG={out}\n"
        f"#   make ran-run     CONFIG={out} TARGET=all CAMPAIGN={campaign}\n")
    out.write_text(header + yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))
    print(f"\nwrote {out}")
    print(f"next:  make ran-doctor CONFIG={out}")
    if not shutil.which("tshark"):
        print("  !! tshark is not installed, run scripts/bootstrap_ranbench.sh first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
