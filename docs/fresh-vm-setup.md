# Fresh VM / Server Setup — `upfbench`

How to stand up the benchmark framework from nothing on a clean Linux box, what each
step does, and how it fits together. Written for someone who has the repo and a fresh
Ubuntu 22.04 VM and wants to run a suite against a UPF.

---

## 0. The mental model (read this first)

> **Prefer containers?** You can skip the host install below and run the framework as a
> Docker image instead — same configs, same behavior. See
> [docker-deployment.md](docker-deployment.md). The rest of this page is the host-based path.

There are **two separate things** on the machine, and it's easy to conflate them:

```
fresh VM
├── upf-benchmark-framework/   ← THE TESTER  (this repo: Python + pfcpsim source)
└── the UPF under test         ← THE TESTED  (SD-Core / OAI / Open5GS, deployed separately)
```

- **The tester** is this repo. It is a **Python package** (no compile step for the Python
  part) plus one **vendored Go tool** (`pfcpsim`) that you build locally. It drives the
  UPF over **N4 (PFCP)** and pushes traffic over **N3 (GTP-U)**, then writes a report.
- **The tested** is a real 5G UPF you deploy on the same box (or a reachable one). The
  framework does **not** install it — that's a separate deployment (see
  [`scripts/`](../scripts/) and the `docs/*-deployment.md` guides).

You connect the two through a **config file** that holds the UPF's N4/N3 addresses.

> **One golden rule:** always run `upfbench` **from the repo root**. Several paths
> (e.g. `pfcpsim_dir: third_party/pfcpsim`) are relative to the current directory.

---

## 1. What travels in the repo vs. what you build locally

When you copy the repo you get **source and configs only** — a few MB of text. Build
artifacts are deliberately **not** committed (see [`.gitignore`](../.gitignore)):

| In the repo (copied) | NOT in the repo (you build/install) |
|---|---|
| `upfbench/` — the Python framework | the `pfcpsim` / `pfcpctl` **binaries** (gitignored) |
| `third_party/pfcpsim/` — pfcpsim **source** | Python deps (PyYAML, Jinja2) |
| `configs/`, `scripts/`, `docs/` | system tools (tcpreplay, tcpdump, docker) |
| `pyproject.toml`, `README.md` | a LaTeX toolchain (for PDF reports) |
| | the **UPF under test** itself |

So "copy the repo" is necessary but not sufficient — you rebuild three things (Python
package, pfcpsim binary, system tools) and deploy a UPF. That's what the rest of this
doc walks through.

> **Note on the vendored pfcpsim:** the copy in `third_party/pfcpsim/` carries our
> UPF-compatibility patches (2-octet Apply Action, DNN/Network-Instance, CHOOSE F-TEID,
> single-QER, keep-association, MBR knob). Building *this* source preserves them; a stock
> upstream pfcpsim would not have them. Always build from the repo, not from `go install`.

---

## 2. Prerequisites

- **OS:** Ubuntu 22.04 LTS (what this was built/tested on). Other Debian-family distros
  are fine; adjust package names.
- **Privileges:** `sudo`. Traffic injection (`tcpreplay`), packet capture (`tcpdump`),
  and most UPF deployments (docker, kernel modules) need root.
- **Network:** the VM must be able to reach the UPF's N4 and N3 IPs. For the usual
  single-box setup the UPF runs in docker on the same host, so this is automatic.

---

## 3. Step-by-step

### Step 1 — Copy the repo onto the VM

```bash
# from your machine:
scp -r upf-benchmark-framework user@NEWVM:~/
#   or on the VM, if it's in git:
git clone <your-repo-url> ~/upf-benchmark-framework
cd ~/upf-benchmark-framework
```
*What it does:* lands the Python code, the pfcpsim source, and all configs. Nothing is
built yet.

---

### Step 2 — Install system packages

```bash
sudo apt update
sudo apt install -y \
    python3 python3-pip python3-venv \
    golang-go \
    tcpreplay tcpdump iproute2 \
    texlive-xetex texlive-fonts-recommended   # optional: for PDF reports
```
*What each is for:*
- **python3 / pip / venv** — runs the framework itself.
- **golang-go** — compiles the `pfcpsim` N4 driver in Step 4. (Go ≥ 1.21; the box this
  was developed on has 1.26.)
- **tcpreplay** — the N3 traffic generator (replays crafted GTP-U frames at line rate).
- **tcpdump** — packet capture used by the datapath probe and to confirm forwarding.
- **iproute2** — `ip` for interface/ARP/route inspection the adapters use.
- **texlive-xetex** — turns the Jinja-generated `.tex` into a `report.pdf`. **Optional:**
  without it you still get `report.tex` (run LaTeX later/elsewhere), the run won't fail.

> If your UPF runs in Docker (SD-Core / OAI / Open5GS all do here), also install Docker
> Engine + the Compose v2 plugin. See the per-UPF deployment doc for specifics.

---

### Step 3 — Install the Python framework

```bash
cd ~/upf-benchmark-framework
python3 -m pip install -e .
```
*What it does:* reads [`pyproject.toml`](../pyproject.toml), installs the two Python deps
(**PyYAML** to read configs, **Jinja2** to render reports), and creates the **`upfbench`**
command on your PATH. `-e` (editable) means it points at the source in place, so any edits
take effect immediately — no reinstall.

*If `upfbench` isn't found afterward* (PATH issue with `--user` installs), you can always
run it as a module instead — identical behavior:
```bash
python3 -m upfbench.cli list
```

---

### Step 4 — Build the pfcpsim N4 driver

```bash
cd ~/upf-benchmark-framework/third_party/pfcpsim
CGO_ENABLED=0 go build -o pfcpsim ./cmd/pfcpsim
CGO_ENABLED=0 go build -o pfcpctl ./cmd/pfcpctl
cd ~/upf-benchmark-framework
```
*What it does:* compiles two Go binaries from the vendored source into
`third_party/pfcpsim/` (exactly where the framework looks for them, per
`pfcpsim_dir: third_party/pfcpsim`):
- **`pfcpsim`** — a gRPC server that holds the PFCP/SMF state and speaks N4 to the UPF.
- **`pfcpctl`** — the client the framework calls to associate, create/modify/delete
  sessions, etc.

`CGO_ENABLED=0` produces a static binary with no libc surprises across distros. First
build downloads Go modules, so it needs internet (or a warm module cache).

---

### Step 5 — Verify the tester is ready

```bash
upfbench list                 # should print the 3 suites and their test-case IDs
./third_party/pfcpsim/pfcpsim --help >/dev/null && echo "pfcpsim OK"
```
*What it does:* `upfbench list` proves the Python side imports cleanly; the second line
proves the Go binary built. At this point the **tester** is fully installed. You still
have no UPF to point it at.

---

### Step 6 — Deploy the UPF under test

This is a separate deployment, one per UPF. Use the bundled scripts/guides:

| UPF | Deploy with |
|---|---|
| Open5GS | [`scripts/deploy_open5gs.sh`](../scripts/deploy_open5gs.sh) + [`docs/open5gs-deployment.md`](open5gs-deployment.md) |
| SD-Core BESS-UPF | aether-onramp (see the SD-Core deployment notes) |
| OAI-UPF | the OAI deployment guide/script |

*What it does:* brings up the actual 5G UPF (and its core) so there's something on N4/N3
to benchmark. Verify it's healthy (PFCP associated, datapath up) before testing.

---

### Step 7 — Point a config at your UPF

Each UPF has a campaign file in [`configs/`](../configs/) (`open5gs.yaml`,
`oai-upf.yaml`, `sdcore-bess.yaml`, …). Open the one for your UPF and check the
addresses match your deployment:

```yaml
upf:
  adapter: open5gs_upf          # which per-UPF plugin to load
  n4_addr: "172.22.0.8:8805"    # the UPF's N4 (PFCP) endpoint  <-- must match deployment
  pfcpsim_iface: br-xxxx        # host interface pfcpsim sends PFCP from
  n3_remote_ip: "172.22.0.8"    # the UPF's N3 (GTP-U) IP        <-- must match deployment
  ue_pool: "10.45.1.0/24"       # UE IP pool inside the UPF's DNN
  ...
```
*What it does:* the **config picks the UPF + parameters**; the **adapter** named here is
the small per-UPF plugin that knows how to talk to that UPF. This is the only file you
normally edit per environment.

---

### Step 8 — Run a suite

```bash
# non-interactive (repeatable):
upfbench run --config configs/open5gs.yaml --suite pfcp          # Suite 3
upfbench run --config configs/open5gs.yaml --suite performance   # Suite 1
upfbench run --config configs/open5gs.yaml --suite load          # Suite 2
upfbench run --config configs/open5gs.yaml --suite all           # all three

# or interactive menu (pick 1-4, then give the config path):
upfbench
```
*What it does:* `runner` reads the config → starts the `pfcpsim` server → loads the
adapter → runs the chosen suite's test cases (driving N4 with `pfcpctl` and N3 with
`tcpreplay`) → collects metrics → writes results and a report.

`--suite` is the suite **selector**; it overrides the `suite:` default baked into the
config. Valid values: `performance` | `load` | `pfcp` | `all`.

---

### Step 9 — Find your results

```bash
ls campaigns/<campaign-id>/        # campaign id comes from the config (e.g. UPF-BM-O5GS-001)
#   results.json   — machine-readable metrics
#   raw/           — raw captures/logs per test
#   report.pdf     — the formatted report (or report.tex if LaTeX wasn't installed)
```
*What it does:* every run drops a self-contained folder under `campaigns/`. The
`raw/` and `*.pdf` are gitignored (results are per-run, not source).

---

## 4. One-shot bootstrap

Steps 2–5 are scripted in [`scripts/bootstrap_fresh_vm.sh`](../scripts/bootstrap_fresh_vm.sh):

```bash
cd ~/upf-benchmark-framework
./scripts/bootstrap_fresh_vm.sh
```
It installs the system packages, `pip install -e .`, builds `pfcpsim`/`pfcpctl`, and runs
the verification. It does **not** deploy a UPF (Step 6) — that's intentionally separate.

---

## 5. Quick reference

```bash
# install tester (once)
sudo apt install -y python3-pip golang-go tcpreplay tcpdump texlive-xetex
pip install -e .
( cd third_party/pfcpsim && go build -o pfcpsim ./cmd/pfcpsim && go build -o pfcpctl ./cmd/pfcpctl )

# deploy the UPF (once, per UPF)
sudo ./scripts/deploy_open5gs.sh      # example

# run (any time) — always from the repo root
upfbench run --config configs/open5gs.yaml --suite pfcp
```

---

## 6. Troubleshooting

- **`upfbench: command not found`** — pip installed to `~/.local/bin` which isn't on PATH.
  Either add it (`export PATH=$PATH:~/.local/bin`) or use `python3 -m upfbench.cli ...`.
- **`pfcpsim` / `pfcpctl` not found at runtime** — you didn't build Step 4, or you're not
  running from the repo root (the default `pfcpsim_dir` is relative). Build them and `cd`
  to the repo root.
- **Permission denied on tcpreplay/tcpdump** — run with `sudo`, or grant the binaries
  `CAP_NET_RAW`.
- **`report.tex` but no `report.pdf`** — LaTeX toolchain absent. Install `texlive-xetex`
  and re-run, or compile the `.tex` elsewhere. The run itself still succeeds.
- **Suite runs but everything fails / no N4 association** — the UPF isn't reachable or the
  config addresses don't match the deployment. Check `n4_addr` / `n3_remote_ip` against
  the running UPF, and that the UPF's PFCP is up.
- **Go build fails offline** — first build fetches modules; pre-warm the module cache or
  build once with internet.
