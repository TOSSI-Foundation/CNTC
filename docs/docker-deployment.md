# Running `upfbench` in Docker

The framework runs **two ways** from the same source:

- **Host-based** — `pip install -e .` + `go build` (see [fresh-vm-setup.md](fresh-vm-setup.md)).
- **Containerized** — a Docker image that bundles the framework, the patched `pfcpsim`,
  and the host tooling it shells out to. This page covers the container.

Both modes use the **same configs** and behave identically, because the container runs with
**host networking** — see "Why host networking" below.

---

## TL;DR

```bash
# build once
docker build -t upfbench:latest .          # or: ./scripts/upfbench-docker.sh (auto-builds)

# run a suite — same UX as the host command
./scripts/upfbench-docker.sh run --config configs/oai-upf.yaml    --suite pfcp   # OAI   (docker)
./scripts/upfbench-docker.sh run --config configs/sdcore-bess.yaml --suite pfcp  # SD-Core (k8s)
./scripts/upfbench-docker.sh run --config configs/open5gs.yaml    --suite pfcp   # Open5GS (docker)
./scripts/upfbench-docker.sh list
```

Results land in `./campaigns/<campaign-id>/` on the host (bind-mounted), exactly like
host-based runs.

---

## What's in the image

Built by the multi-stage [`Dockerfile`](../Dockerfile):

1. **Stage 1 (`golang:1.25`)** compiles `pfcpsim` + `pfcpctl` from the **vendored, patched**
   source in `third_party/pfcpsim/` — so the UPF-compatibility fixes (2-octet Apply Action,
   DNN, CHOOSE F-TEID, single-QER, …) are baked in.
2. **Stage 2 (`python:3.10-slim`)** adds the external tools the adapters drive:
   - `tcpreplay`, `tcpdump`, `iproute2` — N3 traffic + capture + interface inspection
   - **docker CLI** (no daemon) — drives the OAI/Open5GS UPF containers via the host daemon
   - **kubectl** — drives the SD-Core UPF pod (k8s)
   - a **`sudo` shim** — the container runs as root, so `sudo` is a passthrough; this lets the
     *same* configs (which say `sudo docker` / `sudo tcpreplay`) work unchanged in-container
   - the framework itself (`pip install -e .`), entrypoint `upfbench`

PDF reports are **not** rendered in the container (no LaTeX, to keep the image lean): you
still get `results.json` and `report.tex`. Render the PDF on the host (`texlive-xetex`) or
in a fatter image if you need it.

---

## How you run it (and what each flag does)

The wrapper [`scripts/upfbench-docker.sh`](../scripts/upfbench-docker.sh) is the easy path;
under the hood it runs:

```bash
docker run --rm -it \
  --network host \                                   # see host ifaces + reach UPFs like the host
  --cap-add NET_ADMIN --cap-add NET_RAW \            # tcpreplay/tcpdump on host interfaces
  -v /var/run/docker.sock:/var/run/docker.sock \     # drive OAI/Open5GS via host docker daemon
  -v "$PWD/configs:/opt/upfbench/configs" \          # edit configs without rebuilding
  -v "$PWD/campaigns:/opt/upfbench/campaigns" \      # results persist on the host
  -v "$HOME/.kube/config:/root/.kube/config:ro" \    # drive SD-Core's k8s pod
  -e KUBECONFIG=/root/.kube/config \
  upfbench:latest run --config configs/oai-upf.yaml --suite pfcp
```

`docker compose` works too — see [`docker-compose.yml`](../docker-compose.yml):
```bash
docker compose run --rm upfbench run --config configs/oai-upf.yaml --suite pfcp
```

---

## Why host networking (the key design point)

`upfbench` is not a pure-Python app — it puts real packets on the wire:

- **N4 (PFCP)** is sourced from a specific host interface (`pfcpsim_iface`, e.g. `access`
  for SD-Core or `br-xxxx` for the docker cores).
- **N3 (GTP-U)** is injected with `tcpreplay` from `gen_iface` (also a host interface).
- It reaches UPFs at docker-bridge IPs and at `127.0.0.1:6443` (the k8s API for SD-Core).

With `--network host` the container shares the host's network namespace, so it sees
`access` / `core` / `br-xxxx` and can reach `127.0.0.1` exactly like the host. That's what
lets **one config file work identically** in host-based and containerized runs. A bridged
container network would hide those interfaces and break the datapath.

---

## Requirements on the host

- Docker Engine (to run the container) + the UPF you're testing already deployed.
- For SD-Core: a working `~/.kube/config` (or `KUBECONFIG`) whose API server is reachable
  from the host net namespace (here `https://127.0.0.1:6443`).
- The `docker.sock` mount means the container can drive the host's daemon — only run images
  you trust (this is your own build).

---

## Troubleshooting

- **`Cannot connect to the Docker daemon`** inside the run — the socket isn't mounted, or the
  host user can't access `/var/run/docker.sock`. Run the wrapper with the same privileges you
  use for `docker ps`.
- **SD-Core run can't reach k8s** — kubeconfig not mounted or API server not on `127.0.0.1`.
  Check `kubectl get pod -n aether-5gc upf-0` works on the host first.
- **`tcpreplay: ... Operation not permitted`** — add `--cap-add NET_RAW` (the wrapper does);
  if your tcpreplay still refuses, run the container with `--privileged`.
- **No `report.pdf`** — expected; render `campaigns/<id>/report.tex` on the host with LaTeX.
