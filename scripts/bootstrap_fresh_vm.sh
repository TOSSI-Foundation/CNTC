#!/usr/bin/env bash
# bootstrap_fresh_vm.sh — install the upfbench *tester* on a fresh Ubuntu 22.04 box.
#
# Sets up everything EXCEPT the UPF under test (that's a separate deployment; see
# scripts/deploy_*.sh and docs/*-deployment.md). Steps mirror docs/fresh-vm-setup.md:
#   1. system packages   2. python framework   3. build pfcpsim   4. verify
#
# Run from the repo root:  ./scripts/bootstrap_fresh_vm.sh
set -euo pipefail

# resolve repo root from this script's location, regardless of where it's invoked
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
echo ">>> repo root: $REPO_ROOT"

echo ">>> [1/4] system packages (apt)"
sudo apt-get update
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    golang-go \
    tcpreplay tcpdump iproute2 \
    texlive-xetex texlive-fonts-recommended   # texlive-* optional: only needed for PDF reports

echo ">>> [2/4] python framework + dashboard (pip install -e '.[dashboard]')"
# PEP 660 editable installs need setuptools >= 64; older Ubuntu ships 59.x.
python3 -m pip install -U pip setuptools wheel
python3 -m pip install -e '.[dashboard]' \
  || { echo "    editable install failed; installing deps directly"; \
       python3 -m pip install PyYAML Jinja2 scapy dash plotly; }

echo ">>> [3/4] build pfcpsim + pfcpctl (vendored source -> third_party/pfcpsim/)"
( cd third_party/pfcpsim
  CGO_ENABLED=0 go build -o pfcpsim ./cmd/pfcpsim
  CGO_ENABLED=0 go build -o pfcpctl ./cmd/pfcpctl )

echo ">>> [4/4] verify"
if command -v upfbench >/dev/null 2>&1; then
    upfbench list
else
    echo "    (upfbench not on PATH; using module form)"
    python3 -m upfbench.cli list
fi
./third_party/pfcpsim/pfcpsim --help >/dev/null 2>&1 && echo "    pfcpsim binary OK"
./third_party/pfcpsim/pfcpctl --help >/dev/null 2>&1 && echo "    pfcpctl binary OK"
python3 -c "import dash, plotly" 2>/dev/null && echo "    dashboard deps OK (dash + plotly)"

echo ""
echo ">>> done. Next:"
echo "    python3 -m upfbench.cli doctor        # check hardware prereqs (hugepages, VFs, TRex, kubectl)"
echo "    python3 -m upfbench.cli dashboard      # launch the web dashboard"

cat <<'DONE'

>>> tester ready.

Next (separate from this script):
  1. Deploy a UPF:   sudo ./scripts/deploy_open5gs.sh    (or the SD-Core / OAI guide)
  2. Check the config addresses match it:   configs/<upf>.yaml
  3. Run a suite (from the repo root):
        upfbench run --config configs/open5gs.yaml --suite pfcp

See docs/fresh-vm-setup.md for the full explanation of each step.
DONE
