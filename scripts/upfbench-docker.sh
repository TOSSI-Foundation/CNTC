#!/usr/bin/env bash
# upfbench-docker.sh — run the containerized tester with the same UX as the host command.
#
#   ./scripts/upfbench-docker.sh list
#   ./scripts/upfbench-docker.sh run --config configs/oai-upf.yaml --suite pfcp
#   ./scripts/upfbench-docker.sh run --config configs/sdcore-bess.yaml --suite pfcp
#
# Uses host networking so the container sources PFCP/GTP-U from the host's interfaces and
# reaches the UPFs (docker bridges + k8s on 127.0.0.1) exactly like the host-based run.
# Mounts the docker socket (OAI/Open5GS), the kubeconfig (SD-Core), and configs/+campaigns/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${UPFBENCH_IMAGE:-upfbench:latest}"
KUBECONFIG_SRC="${KUBECONFIG:-$HOME/.kube/config}"

# build on first use (or when UPFBENCH_BUILD=1)
if [[ "${UPFBENCH_BUILD:-0}" == "1" ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo ">>> building $IMAGE ..."
    docker build -t "$IMAGE" "$REPO_ROOT"
fi

docker_args=(
    --rm -i
    --network host                         # see host ifaces + reach UPFs like the host does
    --cap-add NET_ADMIN --cap-add NET_RAW  # tcpreplay/tcpdump on host interfaces
    -v /var/run/docker.sock:/var/run/docker.sock   # drive OAI/Open5GS via host daemon
    -v "$REPO_ROOT/configs:/opt/upfbench/configs"
    -v "$REPO_ROOT/campaigns:/opt/upfbench/campaigns"
)
# attach a TTY only when running interactively (so piping/CI still works)
[[ -t 0 && -t 1 ]] && docker_args+=( -t )
# mount kubeconfig if present (SD-Core); harmless when absent
if [[ -f "$KUBECONFIG_SRC" ]]; then
    docker_args+=( -v "$KUBECONFIG_SRC:/root/.kube/config:ro" -e KUBECONFIG=/root/.kube/config )
fi

exec docker run "${docker_args[@]}" "$IMAGE" "$@"
