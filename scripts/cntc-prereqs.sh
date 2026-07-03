#!/usr/bin/env bash
# cntc-prereqs.sh — install everything CNTC needs to test an af_packet SD-Core UPF, then verify.
# Idempotent; safe to re-run. Run from the framework repo root:  sudo ./scripts/cntc-prereqs.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
echo ">>> CNTC prerequisites — repo: $REPO"

# 1) system packages -----------------------------------------------------------
echo ">>> [1/5] system packages (apt)"
sudo apt-get update -y
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    tcpreplay tcpdump iproute2 iputils-ping \
    git curl wget make \
    golang-go            # to build the vendored pfcpsim (needs Go); ignore if you use the image

# 2) python framework + verdict layer + dashboard ------------------------------
echo ">>> [2/5] python deps (framework + cntc + dashboard)"
python3 -m pip install -U pip setuptools wheel
python3 -m pip install -e '.[dashboard]' \
  || python3 -m pip install PyYAML Jinja2 scapy dash plotly   # fallback if editable install fails

# 3) build pfcpsim + pfcpctl (N4 control plane for pfcp/load/n3neg) -------------
echo ">>> [3/5] build pfcpsim + pfcpctl"
if [ ! -x third_party/pfcpsim/pfcpsim ]; then
  ( cd third_party/pfcpsim
    CGO_ENABLED=0 go build -o pfcpsim ./cmd/pfcpsim
    CGO_ENABLED=0 go build -o pfcpctl ./cmd/pfcpctl ) \
    && echo "    built third_party/pfcpsim/{pfcpsim,pfcpctl}" \
    || echo "    WARN: pfcpsim build failed (need Go >= 1.25). pfcp/load/n3neg suites will be skipped."
else
  echo "    pfcpsim already built"
fi

# 4) passwordless sudo for the raw sender (tcpreplay needs root) ----------------
echo ">>> [4/5] passwordless sudo for tcpreplay (raw send)"
if sudo -n true 2>/dev/null; then
  echo "    passwordless sudo already works"
else
  echo "    NOTE: the traffic generator runs 'sudo tcpreplay'. If sudo prompts for a password"
  echo "    during a run it will hang. Add a NOPASSWD rule, e.g.:"
  echo "      echo \"\$USER ALL=(ALL) NOPASSWD: /usr/bin/tcpreplay, /usr/bin/taskset\" | sudo tee /etc/sudoers.d/cntc"
fi

# 5) preflight -----------------------------------------------------------------
echo ">>> [5/5] doctor (preflight)"
export PATH="$PATH:$HOME/.local/bin"
python3 -m upfbench.cli doctor || true

cat <<EOF

>>> DONE. Next:
    export KUBECONFIG=\$HOME/.kube/config
    export PATH=\$PATH:/var/lib/rancher/rke2/bin:\$HOME/.local/bin   # rke2 path if applicable
    kubectl get pods -A | grep -iE 'upf|bess'      # confirm your UPF is reachable
    ./scripts/cntc-configure.py                    # interactive: build your campaign config
    ./scripts/cntc-run-all.sh configs/my-upf.yaml  # run all suites + grade + certify + dashboard
EOF
