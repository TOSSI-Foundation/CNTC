# upfbench — containerized tester. Multi-stage: build the patched pfcpsim, then a lean
# Python runtime with the host-tooling the adapters shell out to (docker CLI, kubectl,
# tcpreplay, tcpdump). Run with --network host so it sources PFCP/GTP-U from the host's
# interfaces and reaches the UPFs exactly like the host-based deployment does.
# See docs/docker-deployment.md.

# ---- Stage 1: build pfcpsim + pfcpctl from the vendored (patched) source ----
FROM golang:1.25-bookworm AS pfcpbuild
WORKDIR /src
# go.mod/go.sum first for layer caching, then the rest of the vendored tree
COPY third_party/pfcpsim/go.mod third_party/pfcpsim/go.sum ./
RUN go mod download
COPY third_party/pfcpsim/ ./
RUN CGO_ENABLED=0 go build -o /out/pfcpsim ./cmd/pfcpsim \
 && CGO_ENABLED=0 go build -o /out/pfcpctl ./cmd/pfcpctl

# ---- Stage 2: runtime ----
FROM python:3.10-slim-bookworm

ARG DOCKER_CLI_VERSION=26.1.4
ARG KUBECTL_VERSION=v1.30.2

# tools the adapters/control/traffic layers invoke as external processes:
#   tcpreplay/tcpdump  - N3 traffic + capture
#   iproute2           - `ip`, `ss`
#   psmisc             - `fuser` (frees the pfcpsim port)
#   procps             - `pkill`/`pgrep` (process control)
#   net-tools          - `arp` (N3 next-hop MAC lookup)
#   iputils-arping/ping - ARP/reachability probes
RUN apt-get update && apt-get install -y --no-install-recommends \
        tcpreplay tcpdump iproute2 psmisc procps net-tools \
        iputils-ping iputils-arping ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# docker CLI only (no daemon) — talks to the host's daemon via the mounted socket
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
 && docker --version

# kubectl — drives the SD-Core UPF pod (k8s)
RUN curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && kubectl version --client=true 2>/dev/null | head -1 || true

# sudo shim: the container runs as root, and the shared configs invoke "sudo docker" /
# "sudo tcpreplay". A passthrough sudo lets those SAME configs work unchanged in-container.
RUN printf '#!/bin/sh\nexec "$@"\n' > /usr/local/bin/sudo && chmod 0755 /usr/local/bin/sudo

WORKDIR /opt/upfbench
COPY pyproject.toml README.md ./
COPY upfbench/ ./upfbench/
COPY configs/ ./configs/
COPY scripts/ ./scripts/
COPY docs/ ./docs/
COPY third_party/ ./third_party/
# drop in the freshly built binaries (the gitignored ones aren't in the build context anyway)
COPY --from=pfcpbuild /out/pfcpsim /out/pfcpctl ./third_party/pfcpsim/

RUN pip install --no-cache-dir -e .

# results + configs are bind-mounted at run time; declare campaigns as a volume default
VOLUME ["/opt/upfbench/campaigns"]

ENTRYPOINT ["upfbench"]
CMD ["list"]
