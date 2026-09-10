#!/usr/bin/env bash
# bootstrap_ranbench.sh: install the ranbench RAN *tester* on a fresh Ubuntu box.
#
# Installs everything the ranbench engine needs to DRIVE tests against a RAN, but NOT the RAN
# under test and NOT the 5G core. Both are bring-your-own:
#   * the RAN (e.g. OCUDU) is the SUBJECT of the certificate: you deploy it, we measure it
#   * the core (AMF for N2, UPF for N3) is a PEER: deploy free5GC/Open5GS/SD-Core separately
# What this script does install is the UE simulator, because the UE is ranbench's stimulus:
# a RAN cannot be exercised without one.
#
# Steps:
#   1. system packages   (tshark/tcpdump for the wire evidence, SCTP, python, build toolchain)
#   2. python framework  (pip install -e .)
#   3. build the UE      (OAI nr-UE + its ZeroMQ radio -> nr-uesoftmodem, liboai_zmqdevif.so)
#   4. verify            (ranbench list + binaries + passwordless sudo)
#
# LICENSING NOTE: the OAI UE is licensed by the OpenAirInterface Software Alliance. This script
# FETCHES and builds it from upstream INTO YOUR environment: it is NOT distributed as part of
# ranbench (which is Apache-2.0). ranbench invokes nr-uesoftmodem as an external process
# (arm's-length), so no license mixing. Same model cpbench uses for UERANSIM.
#
# Run from the repo root:  ./scripts/bootstrap_ranbench.sh
# Knobs (env vars):
#   OAI_DIR=~/openairinterface5g    where to clone/build the OAI UE
#   OAI_REPO=https://gitlab.eurecom.fr/oai/openairinterface5g.git
#   OAI_REF=<tag/branch>            optional pin (default: repo default branch)
#   SKIP_APT=1                      skip the apt step (deps already present)
#   SKIP_UE=1                       skip the UE build (using another UE simulator)
#   FORCE_UE=1                      rebuild the UE even if the binaries already exist
#   FAPI=1                          build the UE for the L1/L2 split (nFAPI) rig instead of
#                                   the CU/DU rig. The two rigs need different UE builds:
#                                     * CU/DU  reaches the O-DU over the ZeroMQ virtual radio,
#                                       so it needs stock OAI plus the oai_zmqdevif plugin.
#                                     * L1/L2  reaches the PNF over the rfsimulator, and its
#                                       single-antenna (1T1R) cell needs an rfsim fix that stock
#                                       OAI does not carry. The stock UE connects to the
#                                       simulator but never synchronises to the cell. The fix is
#                                       public on the TOSSI fork, branch rfsim_ocudu, so FAPI=1
#                                       points the defaults there and skips the ZMQ plugin
#                                       (rfsim is built into nr-uesoftmodem, no plugin needed).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
# FAPI mode changes only the *default* repo/ref/dir; an explicit OAI_* env var still wins.
if [ "${FAPI:-0}" = "1" ]; then
  OAI_DIR="${OAI_DIR:-$HOME/OAI-RAN}"      # separate tree, so it does not clobber a CU/DU clone
  OAI_REPO="${OAI_REPO:-https://github.com/TOSSI-Foundation/OAI-RAN.git}"
  OAI_REF="${OAI_REF:-rfsim_ocudu}"
fi
OAI_DIR="${OAI_DIR:-$HOME/openairinterface5g}"
OAI_REPO="${OAI_REPO:-https://gitlab.eurecom.fr/oai/openairinterface5g.git}"
OAI_BUILD="$OAI_DIR/cmake_targets/ran_build/build"

echo ">>> ranbench bootstrap, repo root: $REPO_ROOT"
echo ">>> OAI UE target dir: $OAI_DIR"

# ---------------------------------------------------------------------------
echo ">>> [1/4] system packages (apt)"
if [ "${SKIP_APT:-0}" = "1" ]; then
  echo "    SKIP_APT=1 -> skipping apt"
else
  sudo apt-get update
  # tshark/tcpdump decode the NGAP/F1AP/E1AP/GTP-U evidence, without them almost every test
  # grades 'na', so they are not optional for ranbench the way they are for cpbench.
  # libzmq3-dev is the virtual radio that replaces RF between the UE and the O-DU.
  sudo apt-get install -y \
      git make gcc g++ cmake ninja-build pkg-config \
      libsctp-dev lksctp-tools libzmq3-dev \
      python3 python3-pip python3-venv \
      iproute2 iputils-ping psmisc curl ca-certificates \
      tcpdump tshark \
    || { echo "!! apt install failed, install the packages above manually"; exit 1; }
fi

# ---------------------------------------------------------------------------
echo ">>> [2/4] python framework (pip install -e .)"
python3 -m pip install -U pip setuptools wheel
python3 -m pip install -e . \
  || { echo "    editable install failed; installing deps directly"; \
       python3 -m pip install PyYAML Jinja2 'httpx[http2]>=0.27' scapy; }

# ---------------------------------------------------------------------------
echo ">>> [3/4] OAI nr-UE + ZeroMQ radio (fetched + built into your environment, not bundled)"
# The UE is the stimulus for every RAN campaign. OAI as a *product under test* additionally
# needs the gNB binaries, which OAI_GNB=1 builds below.
# The FAPI rig reaches the PNF over the rfsimulator, which is built into nr-uesoftmodem, so it
# needs no ZeroMQ device plugin. The CU/DU rig does. "already built" and the ZMQ step below
# both key off this.
if [ "${FAPI:-0}" = "1" ]; then
  NEED_ZMQ=0
else
  NEED_ZMQ=1
fi
_ue_built() {
  [ -x "$OAI_BUILD/nr-uesoftmodem" ] || return 1
  [ "$NEED_ZMQ" = "1" ] && [ ! -f "$OAI_BUILD/liboai_zmqdevif.so" ] && return 1
  return 0
}

if [ "${SKIP_UE:-0}" = "1" ]; then
  echo "    SKIP_UE=1 -> skipping"
elif _ue_built && [ "${FORCE_UE:-0}" != "1" ]; then
  echo "    already built at $OAI_BUILD (set FORCE_UE=1 to rebuild) -> skipping"
else
  if [ ! -d "$OAI_DIR/.git" ]; then
    # Clone the requested ref directly. A shallow clone of the default branch cannot then
    # `checkout` a different branch (its objects were never fetched), so when OAI_REF is a
    # branch it has to be named at clone time.
    if [ -n "${OAI_REF:-}" ]; then
      echo "    cloning $OAI_REPO ($OAI_REF) -> $OAI_DIR  (shallow)"
      git clone --depth 1 -b "$OAI_REF" "$OAI_REPO" "$OAI_DIR"
    else
      echo "    cloning $OAI_REPO -> $OAI_DIR  (shallow)"
      git clone --depth 1 "$OAI_REPO" "$OAI_DIR"
    fi
  elif [ -n "${OAI_REF:-}" ]; then
    # Existing clone: fetch the ref specifically (works whether it is a tag or a branch).
    ( cd "$OAI_DIR" && git fetch --depth 1 origin "$OAI_REF" && git checkout FETCH_HEAD )
  fi
  echo "    building nr-uesoftmodem (installs OAI's own deps; this takes a while)"
  ( cd "$OAI_DIR/cmake_targets" && sudo ./build_oai -I --nrUE -w SIMU --ninja )
  if [ "$NEED_ZMQ" = "1" ]; then
    # OAI gates its ZeroMQ radio behind a cmake option that defaults OFF, so the device target
    # does not exist until the build tree is reconfigured. Without it the UE cannot reach the
    # O-DU at all ("unknown target oai_zmqdevif"). The FAPI rig uses --rfsim instead and skips this.
    echo "    enabling and building the ZeroMQ radio (-DOAI_ZMQ=ON)"
    ( cd "$OAI_BUILD" && sudo cmake . -DOAI_ZMQ=ON && sudo cmake --build . --target oai_zmqdevif )
  else
    echo "    FAPI=1 -> rfsim is built into nr-uesoftmodem; skipping the ZeroMQ device"
  fi
fi

# The gNB binaries, only when OAI is the stack under test rather than just the UE.
# Off by default: this is a long build, and a rig that certifies OCUDU needs only the UE above.
# Note the 5gdefault cmake preset does NOT include nr-cuup, so the targets are named directly.
if [ "${OAI_GNB:-0}" = "1" ]; then
  echo ">>> [3b/4] OAI gNB (nr-softmodem + nr-cuup), for certifying OAI itself"
  if [ -x "$OAI_BUILD/nr-softmodem" ] && [ -x "$OAI_BUILD/nr-cuup" ] && [ "${FORCE_UE:-0}" != "1" ]; then
    echo "    already built -> skipping"
  else
    echo "    building nr-softmodem and nr-cuup (long: ~1400 compile steps)"
    ( cd "$OAI_BUILD" && sudo ninja nr-softmodem nr-cuup )
  fi
else
  echo "    (set OAI_GNB=1 to also build nr-softmodem + nr-cuup and certify OAI itself)"
fi

# ---------------------------------------------------------------------------
echo ">>> [4/4] verify"
ok=1
if command -v ranbench >/dev/null 2>&1; then ranbench list; else python3 -m ranbench.cli list; fi
python3 -c "import yaml" 2>/dev/null && echo "    python deps OK (yaml)" || { echo "!! python deps MISSING"; ok=0; }
command -v tshark >/dev/null 2>&1 && echo "    tshark OK ($(command -v tshark))" \
  || { echo "!! tshark MISSING, the protocol/security tests cannot decode evidence"; ok=0; }
# ranbench itself runs unprivileged; the adapter and UE driver elevate per command, so
# passwordless sudo is required or every start/stop/capture silently fails.
if sudo -n true 2>/dev/null; then
  echo "    passwordless sudo OK"
else
  echo "!! 'sudo -n' does not work, ranbench starts/stops the RAN and captures as root."
  echo "   Add a NOPASSWD rule for your user, or run the campaign as root."
  ok=0
fi
if [ "${SKIP_UE:-0}" != "1" ]; then
  if _ue_built; then
    if [ "$NEED_ZMQ" = "1" ]; then
      echo "    OAI UE OK ($OAI_BUILD/nr-uesoftmodem + liboai_zmqdevif.so)"
    else
      echo "    OAI UE OK ($OAI_BUILD/nr-uesoftmodem, rfsim built in)"
    fi
  else
    echo "!! OAI UE binaries missing at $OAI_BUILD"; ok=0
  fi
fi

cat <<DONE

>>> ranbench tester ready.  (The OAI UE is fetched separately; ranbench is Apache-2.0.)

Next (separate from this script, bring your own RAN and core):
  1. Deploy the RAN under test (e.g. OCUDU: ocucp + ocuup + odu) and a 5G core.
  2. Point the campaign config at your rig:   configs/ocudu-ran.yaml
       - ran.bin_dir / ran.configs.*   where your RAN's binaries and configs are
       - core.adapter                  your core (the N2/N3 peer)
       - subscribers                   the SIM your core is provisioned with
       - drivers.ue_bin                $OAI_BUILD/nr-uesoftmodem
       - drivers.*                     the RF parameters, these MUST match your DU's cell
     The eight parameters that have to agree between the DU and the UE, and what breaks when
     they don't, are documented in docs/RANBENCH-RIG.md.
  3. Preflight, then run:
       make ran-doctor  CONFIG=configs/ocudu-ran.yaml            # must say READY
       make ran-run     CONFIG=configs/ocudu-ran.yaml TARGET=all CAMPAIGN=MY-RAN-001
       make ran-certify CAMPAIGN=MY-RAN-001 TARGET=cuup          # cert iff every essential passed

For the L1/L2 split (PNF / VNF over nFAPI) instead of the CU/DU rig, this script was run
with FAPI=1, which built the UE from the fork the rfsimulator cell needs. Use
configs/fapi-split.yaml, set drivers.ue_bin to $OAI_BUILD/nr-uesoftmodem, and follow
docs/FAPI-SPLIT-RIG.md. The stock OAI UE does not synchronise to that 1T1R cell.
DONE
[ "$ok" = "1" ] || { echo "!! some checks failed, see above"; exit 1; }
