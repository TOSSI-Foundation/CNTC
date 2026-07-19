#!/usr/bin/env bash
# eupf-certify.sh — run BOTH UPF certificates for a free5GC + eUPF (eBPF/XDP) deployment:
#   1. Universal Conformance     (suite=conformance, profile=conformance)  — every UPF earns this
#   2. eBPF/XDP Dataplane Assurance (suite=ebpf,     profile=upf-ebpf)      — eBPF UPFs only
# Each is graded and (if it PASSes every essential) issued a certificate.
#
# Usage:  sudo ./scripts/eupf-certify.sh [config.yaml] [campaign-prefix]
#   defaults: configs/eupf.yaml   EUPF-$(HHMMSS)
# The RUN steps need sudo (pfcpsim + microk8s kubectl + tcpreplay); verdict/certify do not.
set -euo pipefail

CFG="${1:-configs/eupf.yaml}"
PREFIX="${2:-EUPF-$(date +%H%M%S 2>/dev/null || echo 001)}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
export PATH="$PATH:/var/lib/rancher/rke2/bin:/usr/local/bin:/snap/bin:$HOME/.local/bin"

CONF="${PREFIX}-CONF"
EBPF="${PREFIX}-EBPF"

echo ">>> [1/4] Universal Conformance  (suite=conformance)  campaign: $CONF"
python3 -m upfbench.cli run --config "$CFG" --suite conformance --profile conformance --campaign "$CONF"

echo ">>> [2/4] eBPF/XDP Dataplane Assurance  (suite=ebpf)  campaign: $EBPF"
python3 -m upfbench.cli run --config "$CFG" --suite ebpf --profile upf-ebpf --campaign "$EBPF"

echo ">>> [3/4] grade + certify — Conformance"
python3 -m cntc.cli verdict "campaigns/$CONF/results.json" --profile conformance --write-back || true
python3 -m cntc.cli certify "campaigns/$CONF/results.json" --profile conformance || true

echo ">>> [4/4] grade + certify — eBPF/XDP"
python3 -m cntc.cli verdict "campaigns/$EBPF/results.json" --profile upf-ebpf --write-back || true
python3 -m cntc.cli certify "campaigns/$EBPF/results.json" --profile upf-ebpf || true

cat <<EOF

>>> DONE.
    Conformance:  campaigns/$CONF/{results.json, scorecard.md, certificate.*}
    eBPF/XDP:     campaigns/$EBPF/{results.json, scorecard.md, certificate.*}
    An eUPF that passes both earns TWO certificates. A non-eBPF UPF earns Conformance and
    is INCOMPLETE (no certificate) on eBPF/XDP — that is the correct, honest outcome.
EOF
