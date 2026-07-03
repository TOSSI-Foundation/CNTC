# upfbench — Benchmarking Guide (run, reproduce, what we did)

One place that explains **how to run the framework** (host or Docker), **how to get
trustworthy/reproducible numbers** (the per-suite reset hook), and **what changed** during
the multi-UPF + containerization work. For first-time setup see
[fresh-vm-setup.md](fresh-vm-setup.md) (host) and [docker-deployment.md](docker-deployment.md)
(container); for the SD-Core-specific operational detail see [RUNBOOK.md](RUNBOOK.md).

---

## 1. What this is

`upfbench` points at an already-running 5G UPF, runs a test **suite**, and writes a
standards-aligned report. It is UPF-agnostic: each UPF is one small adapter
(`upfbench/adapters/<name>.py`); the suites never change.

Three suites:
| # | suite | measures | drives |
|---|-------|----------|--------|
| 1 | `performance` | throughput (NDR/PDR), latency, burst, multi-flow | rules + tcpreplay (GTP-U) |
| 2 | `load` | session capacity, per-UE throughput, latency-under-load | pfcpsim (N sessions) + tcpreplay |
| 3 | `pfcp` | N4 conformance (TS 29.244) — pass/fail | pfcpsim only (no traffic) |

Validated UPFs: **SD-Core BESS-UPF** (k8s), **OAI-UPF** (docker), **Open5GS-UPF** (docker, control-plane).

---

## 2. Two ways to run — same configs, same results

The framework runs **host-based** or **as a Docker container**. Both use the same
`configs/*.yaml` and produce the same numbers (the container runs with `--network host`,
so it sees the host's interfaces and reaches every UPF exactly like the host does).

### Host-based
```bash
cd ~/Shiva/upf-benchmark-framework
export KUBECONFIG=$HOME/.kube/config
python3 -m upfbench.cli run --config configs/sdcore-bess.yaml --suite all
python3 -m upfbench.cli run --config configs/oai-upf.yaml     --suite all
python3 -m upfbench.cli run --config configs/open5gs.yaml     --suite pfcp
python3 -m upfbench.cli list          # show suites + test cases
```
(One-time host setup: `pip install -e .` + build pfcpsim — see fresh-vm-setup.md, or run
`scripts/bootstrap_fresh_vm.sh`.)

### Docker
```bash
docker build -t upfbench:latest .      # once (or let the wrapper auto-build)

# SD-Core (needs the kubeconfig passed through):
sudo env KUBECONFIG=$HOME/.kube/config ./scripts/upfbench-docker.sh \
        run --config configs/sdcore-bess.yaml --suite all
# OAI / Open5GS (docker UPFs):
./scripts/upfbench-docker.sh run --config configs/oai-upf.yaml  --suite all
./scripts/upfbench-docker.sh run --config configs/open5gs.yaml  --suite pfcp
```
The wrapper runs the image with `--network host`, `NET_ADMIN/NET_RAW`, and mounts the
docker socket + kubeconfig + `configs/` + `campaigns/`. See docker-deployment.md.

`--suite` is `performance | load | pfcp | all`. Output lands in `campaigns/<campaign-id>/`
(`results.json` + per-suite `report-*.pdf`/`.tex`), on the host in both modes.

---

## 3. Getting trustworthy numbers — the reset hook

**Why it exists.** A UPF's datapath degrades under back-to-back stress: the `performance`
suite's saturating traffic can **crash SD-Core's `bessd`** (it restarts forwarding ~0), and
its session churn **wedges OAI's session table** (so a following `load`/`pfcp` suite can't
establish). So a naive `--suite all` could have one suite poison the next — you'd see
`0 forwarded`, `capacity 0`, or `CF-02 fail` even though each suite passes on a clean UPF.
(This is the "start from a clean UPF" guidance in RUNBOOK §7, now automated.)

**What it does.** With `reset_between_suites: true` in the config, the runner calls
`adapter.reset()` **before each suite**, returning the UPF to a clean state and blocking
until it's ready:
- **SD-Core** → `kubectl delete pod upf-0` (StatefulSet recreates a fresh `bessd`) + wait Ready
- **OAI / Open5GS** → `docker restart <container>` + wait healthy

It is enabled by default in `configs/sdcore-bess.yaml`, `oai-upf.yaml`, `open5gs.yaml`. It
works in **both** host and Docker mode (in the container it resets via the mounted kubeconfig
/ docker socket). A failed reset warns but does not abort the run.

To disable (e.g. a quick `pfcp`-only run where you don't want a 90 s pod reset):
```yaml
reset_between_suites: false
```

**For publication-grade absolute throughput**, also quiet the host: run **one UPF at a
time** with the others stopped (af_packet throughput is very CPU-sensitive). E.g. for
SD-Core, stop the OAI + Open5GS containers; for OAI, scale SD-Core to 0
(`kubectl scale statefulset/upf -n aether-5gc --replicas=0`) to stop `bessd`'s busy-poll.
Restore them afterward.

---

## 4. Per-UPF specifics

| | SD-Core BESS-UPF | OAI-UPF | Open5GS-UPF |
|---|---|---|---|
| runs as | k8s pod `aether-5gc/upf-0` | docker `oai-upf` | docker `upf` |
| reached over | `kubectl exec` + N4 `127.0.0.1:8805` | `docker exec` + N4 `192.168.70.135` | `docker exec` + N4 `172.22.0.8` |
| N3 generator iface | `access` (host veth) | `demo-oai` bridge | `br-…` bridge |
| reset() | delete pod + wait Ready | restart container | restart container |
| data-plane suites | ✓ perf + load | ✓ perf + load | control-plane only (gtp5g) |
| Suite 3 (pfcp) | ✓ 5/5 | ✓ 5/5 | ✓ 5/5 |

> **Open5GS note:** its 5G UPF only programs the gtp5g kernel datapath for sessions from
> its own SMF, so pfcpsim-driven Suites 1/2 don't forward (it returns GTP-U Error
> Indications). Open5GS is validated at the **control plane** (Suite 3 = 5/5) and via a real
> UERANSIM end-to-end test. See open5gs-deployment notes.

---

## 5. Reproducing the validated baselines

The original validated numbers (2026-06-17) live in each campaign's `report-all.pdf`:
- **SD-Core:** NDR 0.045 Mpps, latency 73/214 µs, LT-01 5000, LT-02 0.0211 Mpps, CF 5/5
- **OAI:** NDR 0.0112 Mpps, LT-01 capacity 250, LT-02 0.0281 Mpps, CF 5/5

To reproduce on demand (clean), per UPF:
```bash
# 1. isolate: stop the other UPFs so the host is quiet (af_packet is CPU-sensitive)
sudo docker stop oai-upf <open5gs containers>      # when testing SD-Core
# (when testing OAI: kubectl scale statefulset/upf -n aether-5gc --replicas=0)

# 2. run all suites — the reset hook gives each suite a fresh UPF
sudo env KUBECONFIG=$HOME/.kube/config ./scripts/upfbench-docker.sh \
        run --config configs/sdcore-bess.yaml --suite all

# 3. restore the others
sudo docker start oai-upf <open5gs containers>
# (kubectl scale statefulset/upf --replicas=1)
```
With the reset hook + isolation, both UPFs reproduce (and SD-Core, on a quiet host, can
exceed) the baselines in a single `--suite all` run. Use a fresh `campaign:` id in the
config if you want to keep the original reports untouched.

Absolute af_packet NDR is **run-to-run noisy** and **host-load-dependent** — raise
`performance.trial_duration_s` (e.g. 20–30) for steadier numbers, and keep the host quiet.
The structural results (capacity, forwarding-verified, latency, CF pass/fail) are stable.

---

## 6. Outputs
```
campaigns/<campaign-id>/
  results.json            # machine-readable KPIs + per-test tables + captured commands
  report-performance.pdf  # one per suite that ran (.tex if no LaTeX toolchain)
  report-load.pdf
  report-pfcp.pdf
  report-all.pdf          # combined (when --suite all)
  raw/                    # pfcpsim/pybess logs, pcaps, etc.
```
The Docker image has no LaTeX (kept lean) → it emits `.tex`; render the PDF on the host
(`texlive-xetex`) if needed.

---

## 7. What we did (change record)

The framework began as SD-Core-only, host-based. This work added:

1. **More UPFs (portability proof).** OAI-UPF and Open5GS-UPF adapters + configs. Made the
   pfcpsim N4 path portable across UPFs via env-gated knobs (`PFCPSIM_NO_URR`,
   `PFCPSIM_APPLY_ACTION_2B`, `PFCPSIM_DNN`, `PFCPSIM_FTEID_CHOOSE`, `PFCPSIM_QFI`,
   `PFCPSIM_SINGLE_QER`) so one driver works against OMEC/BESS, OAI, and Open5GS.

2. **Docker deployment.** Multi-stage `Dockerfile` (builds the patched pfcpsim, then a lean
   Python runtime with docker CLI + kubectl + tcpreplay + a `sudo` shim), `docker-compose.yml`,
   and `scripts/upfbench-docker.sh`. Runs with `--network host` so the **same configs** work
   host or container. Added `scapy` to `pyproject.toml` (Suites 1/2 import it to craft GTP-U).

3. **Reset hook.** `UPFAdapter.reset()` + `reset_between_suites` flag + runner wiring, so
   `--suite all` resets the UPF between suites and reproduces baselines hands-off. This fixed
   the cross-suite degradation (bessd crash / OAI session wedge) that otherwise made a
   back-to-back run report 0-forwarded / capacity-0 / CF-fail.

4. **Validation.** Suites 1/2/3 confirmed for SD-Core + OAI in **both** host and Docker, with
   results matching the 06-17 baselines once each UPF starts clean. Documented the honest
   limits (Open5GS data-plane via pfcpsim; af_packet noise; single-NIC VM rig).

### Extending the tool (so a future change "just works")
- **New Python `import`** → add it to `pyproject.toml` `dependencies` (both `pip install -e .`
  and `docker build` then pick it up).
- **New system CLI** → add it to the `Dockerfile` apt/curl line **and** the host setup.
- **New UPF** → drop `upfbench/adapters/<x>.py` (implement `describe`, `port_counters`,
  optionally `fwd_field`, `reset`) + a `configs/<x>.yaml`. Suites don't change. Framework
  code + configs are auto-copied by `docker build`.
