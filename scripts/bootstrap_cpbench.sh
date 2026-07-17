#!/usr/bin/env bash
# bootstrap_cpbench.sh — install the cpbench control-plane *tester* on a fresh Ubuntu 22.04 box.
#
# Installs everything the cpbench engine needs to DRIVE tests — but NOT the 5G core under test
# (that is bring-your-own; deploy free5GC/open5GS/OAI separately, then point the config at it).
#
# Steps:
#   1. system packages     (build toolchain for UERANSIM, SCTP, docker CLI, net tools)
#   2. python framework     (pip install -e .  -> pulls httpx[http2], PyYAML, scapy ...)
#   3. build UERANSIM       (git clone + cmake build -> nr-gnb / nr-ue)   [external, AGPL-3.0]
#   4. verify               (cpbench list + binaries present)
#
# LICENSING NOTE: UERANSIM is AGPL-3.0. This script FETCHES and builds it from its upstream
# repo INTO YOUR environment — it is NOT distributed as part of cpbench (which is Apache-2.0).
# cpbench invokes nr-gnb/nr-ue as external processes (arm's-length), so no license mixing.
#
# Run from the repo root:  ./scripts/bootstrap_cpbench.sh
# Knobs (env vars):
#   UERANSIM_DIR=~/UERANSIM         where to clone/build UERANSIM
#   UERANSIM_REPO=https://github.com/aligungr/UERANSIM.git
#   UERANSIM_REF=<tag/branch>       optional pin (default: repo default branch)
#   SKIP_APT=1                      skip the apt step (deps already present)
#   SKIP_UERANSIM=1                 skip UERANSIM (e.g. using gnbsim/PacketRusher instead)
#   FORCE_UERANSIM=1                rebuild UERANSIM even if nr-gnb/nr-ue already exist
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
UERANSIM_DIR="${UERANSIM_DIR:-$HOME/UERANSIM}"
UERANSIM_REPO="${UERANSIM_REPO:-https://github.com/aligungr/UERANSIM.git}"
CMAKE_MIN="3.17"   # UERANSIM CMakeLists.txt requires >= 3.17

# Ensure a cmake >= CMAKE_MIN is on PATH. Ubuntu 22.04 apt ships 3.22 (ok), but 18.04/20.04
# ship 3.10/3.16 (too old — the UERANSIM README warns about this). If apt's is too old or
# missing, install a modern cmake via pip (portable: no snap dependency) and put it first on PATH.
ensure_cmake() {
  local ver
  ver="$(cmake --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
  if [ -n "$ver" ] && [ "$(printf '%s\n%s\n' "$CMAKE_MIN" "$ver" | sort -V | head -1)" = "$CMAKE_MIN" ]; then
    echo "    cmake $ver (>= $CMAKE_MIN) OK"
    return 0
  fi
  echo "    cmake ${ver:-not found} < $CMAKE_MIN — UERANSIM needs >= $CMAKE_MIN; installing a modern cmake via pip"
  python3 -m pip install --user "cmake>=$CMAKE_MIN"
  export PATH="$HOME/.local/bin:$PATH"
  hash -r 2>/dev/null || true
  ver="$(cmake --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
  [ -n "$ver" ] && echo "    cmake now: $ver" || { echo "!! still no usable cmake"; return 1; }
}
echo ">>> cpbench bootstrap — repo root: $REPO_ROOT"
echo ">>> UERANSIM target dir: $UERANSIM_DIR"

# ---------------------------------------------------------------------------
echo ">>> [1/4] system packages (apt)"
if [ "${SKIP_APT:-0}" = "1" ]; then
  echo "    SKIP_APT=1 -> skipping apt"
else
  sudo apt-get update
  # UERANSIM build deps: make gcc g++ (+ cmake, version-checked separately below).
  # UERANSIM RUNTIME deps: libsctp-dev lksctp-tools iproute2 (needed to run nr-gnb/nr-ue, not
  # just to build them). Plus python, docker CLI (free5gc adapter), and net tools for cpbench.
  # tcpdump + tshark are used by the AMF NAS-security wire checks (AMF-SEC-01/02); if you
  # don't run the AMF suite they're optional and those tests just grade 'na'.
  sudo apt-get install -y \
      git make gcc g++ cmake libsctp-dev lksctp-tools \
      python3 python3-pip python3-venv \
      iproute2 iputils-ping psmisc curl ca-certificates \
      tcpdump tshark \
    || { echo "!! apt install failed — install the packages above manually"; exit 1; }
  # docker CLI is used by the free5gc adapter to read NF liveness/IPs. Best-effort: many
  # environments already have it (or use k8s), so warn rather than fail if it can't install.
  if ! command -v docker >/dev/null 2>&1; then
    sudo apt-get install -y docker.io || echo "!! could not install docker.io — install docker yourself if your core is docker-based"
  fi
fi

# ---------------------------------------------------------------------------
echo ">>> [2/4] python framework (pip install -e .)"
python3 -m pip install -U pip setuptools wheel
python3 -m pip install -e . \
  || { echo "    editable install failed; installing deps directly"; \
       python3 -m pip install 'httpx[http2]>=0.27' PyYAML Jinja2 scapy; }

# ---------------------------------------------------------------------------
echo ">>> [3/4] UERANSIM (AGPL-3.0 — fetched + built into your environment, not bundled)"
if [ "${SKIP_UERANSIM:-0}" = "1" ]; then
  echo "    SKIP_UERANSIM=1 -> skipping"
elif [ -x "$UERANSIM_DIR/build/nr-gnb" ] && [ -x "$UERANSIM_DIR/build/nr-ue" ] && [ "${FORCE_UERANSIM:-0}" != "1" ]; then
  echo "    already built at $UERANSIM_DIR/build (set FORCE_UERANSIM=1 to rebuild) -> skipping"
else
  if [ ! -d "$UERANSIM_DIR/.git" ]; then
    echo "    cloning $UERANSIM_REPO -> $UERANSIM_DIR"
    git clone "$UERANSIM_REPO" "$UERANSIM_DIR"
  fi
  if [ -n "${UERANSIM_REF:-}" ]; then
    ( cd "$UERANSIM_DIR" && git fetch --all --tags && git checkout "$UERANSIM_REF" )
  fi
  ensure_cmake || { echo "!! cannot get cmake >= $CMAKE_MIN; install it manually (snap install cmake --classic)"; exit 1; }
  echo "    building UERANSIM (this can take a few minutes)"
  ( cd "$UERANSIM_DIR" && make )
fi

# ---------------------------------------------------------------------------
echo ">>> [4/4] verify"
if command -v cpbench >/dev/null 2>&1; then cpbench list; else python3 -m cpbench.cli list; fi
ok=1
python3 -c "import httpx, h2, yaml" 2>/dev/null && echo "    python deps OK (httpx+h2+yaml)" || { echo "!! python deps MISSING"; ok=0; }
if [ "${SKIP_UERANSIM:-0}" != "1" ]; then
  if [ -x "$UERANSIM_DIR/build/nr-gnb" ] && [ -x "$UERANSIM_DIR/build/nr-ue" ]; then
    echo "    UERANSIM OK ($UERANSIM_DIR/build/nr-gnb, nr-ue)"
  else
    echo "!! UERANSIM binaries missing at $UERANSIM_DIR/build"; ok=0
  fi
fi

cat <<DONE

>>> cpbench tester ready.  (UERANSIM is AGPL-3.0, fetched separately; cpbench is Apache-2.0.)

Next (separate from this script — bring your own 5G core):
  1. Deploy a core:   e.g. free5GC via docker-compose (~/free5gc-compose)
  2. Edit the config addresses to match it:   configs/free5gc-cp.yaml
       - core.endpoints / drivers.amf_n2_addr / drivers.gnb_link_ip   (read them live!)
       - set drivers.ueransim_dir=$UERANSIM_DIR if you cloned elsewhere
  3. Preflight, then run:
       cpbench doctor --config configs/free5gc-cp.yaml     # must say READY
       cpbench run    --config configs/free5gc-cp.yaml --nf all

See docs/PLAN-CONTROL-PLANE.md for the test model.
DONE
[ "$ok" = "1" ] || { echo "!! some checks failed — see above"; exit 1; }
