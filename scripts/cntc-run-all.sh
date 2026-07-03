#!/usr/bin/env bash
# cntc-run-all.sh — full e2e: run every suite, merge, grade, certify, (optionally) launch dashboard.
# Usage:  ./scripts/cntc-run-all.sh <config.yaml> [campaign-id]
set -euo pipefail

CFG="${1:?usage: cntc-run-all.sh <config.yaml> [campaign-id]}"
CAMP="${2:-MY-UPF-$(date +%H%M%S 2>/dev/null || echo 001)}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"

export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
export PATH="$PATH:/var/lib/rancher/rke2/bin:/usr/local/bin:$HOME/.local/bin"

echo ">>> [1/5] performance + load + PFCP  (campaign: $CAMP)"
python3 -m upfbench.cli run --config "$CFG" --suite all --campaign "$CAMP"

echo ">>> [2/5] N3 robustness  (may crash+recover the UPF)"
python3 -m upfbench.cli run --config "$CFG" --suite n3neg --campaign "${CAMP}-N3"

echo ">>> [3/5] merge n3neg into the main campaign"
python3 - "$CAMP" <<'PY'
import json, sys
camp = sys.argv[1]
a = json.load(open(f"campaigns/{camp}/results.json"))
n = json.load(open(f"campaigns/{camp}-N3/results.json"))
ns = [s for s in n["suites"] if s["suite"] == "n3neg"][0]
a["suites"] = [s for s in a["suites"] if s["suite"] != "n3neg"] + [ns]
a.pop("verdict", None)
json.dump(a, open(f"campaigns/{camp}/results.json", "w"), indent=2, default=str)
print(f"    merged -> {sum(len(s['tests']) for s in a['suites'])} test cases")
PY

echo ">>> [4/5] grade -> scorecard + verdict"
python3 -m cntc.cli verdict "campaigns/$CAMP/results.json" --write-back || true

echo ">>> [5/5] certificate (issues only on PASS)"
python3 -m cntc.cli certify "campaigns/$CAMP/results.json" || true

cat <<EOF

>>> DONE — campaign: $CAMP
    scorecard:  campaigns/$CAMP/scorecard.md   (+ .html)
    verdict:    campaigns/$CAMP/results.json   (verdict block)
    cert:       campaigns/$CAMP/certificate.*  (present only if PASS)
    dashboard:  python3 -m upfbench.cli dashboard    # http://<host>:8050  (or run in tmux)
EOF
