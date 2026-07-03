# Run CNTC in Kubernetes — live dashboard + in-cluster test runner

Two pieces, sharing one `campaigns/` volume:

```
                  ┌─────────────────────────────────────────────┐
                  │  campaigns/  (shared volume: hostPath / RWX PVC) │
                  └──────────────▲──────────────────────▲─────────┘
                                 │ writes results        │ reads live
                ┌────────────────┴───────┐     ┌─────────┴──────────┐
                │  test runner (Job/CLI) │     │  cntc-dashboard    │
                │  perf/load/pfcp/n3neg  │     │  (Deployment+Svc)  │
                └────────────────────────┘     │  auto-refresh 15s  │
                                               └────────────────────┘
```

**The "live" part:** the dashboard reads `campaigns/` fresh and now **auto-refreshes the runs
list every 15 s** (`UPFBENCH_DASH_LIVE_MS`). So *any* run that writes to the shared volume —
whether from the host CLI (`scripts/cntc-run-all.sh`) or the in-cluster Job — appears on the
dashboard automatically, no manual reload. Set `UPFBENCH_DASH_LIVE_MS=0` to disable.

## 0. Build the image + load it into the cluster's containerd (once)
The dashboard has a dedicated lightweight image (Python + dash/plotly + dashboard/cntc/upfbench):
```bash
docker build -t cntc-dashboard:v1 -f deploy/k8s/Dockerfile.dashboard .
```
Load it into the node's containerd so the kubelet can use it **without a registry**:
```bash
# RKE2 / k3s — MUST target RKE2's own containerd socket (NOT the system one):
docker save -o /tmp/dash.tar cntc-dashboard:v1
sudo /var/lib/rancher/rke2/bin/ctr --address /run/k3s/containerd/containerd.sock \
     -n k8s.io images import /tmp/dash.tar
# (plain containerd: sudo ctr -n k8s.io images import /tmp/dash.tar)
```
> Gotcha: RKE2 runs its **own** containerd at `/run/k3s/containerd/containerd.sock`. Importing
> into the default `/run/containerd/...` gives the pod `ErrImageNeverPull`. The manifest uses
> `imagePullPolicy: Never` because the image is local (no registry).

## 1. Live dashboard (recommended — clean, low-privilege)
Edit `cntc-dashboard.yaml`: set `<IMAGE>` and `<CAMPAIGNS_HOST_DIR>` (the `campaigns/` dir on the
node where you run tests — so host‑CLI runs and the dashboard share it), then:
```bash
kubectl apply -f cntc-dashboard.yaml
kubectl -n cntc get pods
# open  http://<node-ip>:30850
```
Now run tests **however you like** (the simple host flow works great):
```bash
./scripts/cntc-run-all.sh configs/my-upf.yaml MY-UPF-001   # writes to the shared campaigns/
```
…and watch them land on the dashboard live.

## 2. In-cluster test runner (advanced — needs UPF-node access)
Running the *datapath* suite from a pod is possible but has real requirements, because it injects
af_packet traffic into the UPF's access macvlan and execs into the UPF pod:

- `hostNetwork: true` — to see the host access macvlan that reaches the UPF N3
- `privileged: true` — raw send (tcpreplay)
- `nodeName: <UPF node>` — same L2 as the UPF's access interface
- RBAC to `pods/exec` in the UPF namespace + a kubeconfig (mounted, or a scoped Secret)

Edit `cntc-testrunner-job.yaml` (`<IMAGE>`, `<UPF_NODE>`, `<CAMPAIGNS_HOST_DIR>`, config path,
namespace in the ClusterRole), bake your `configs/my-upf.yaml` into the image (or mount it), then:
```bash
kubectl apply -f cntc-testrunner-job.yaml
kubectl -n cntc logs -f job/cntc-run
```
Results stream to the shared `campaigns/` → the dashboard shows them live.

> **Honest caveat.** The **dashboard-in-a-pod** is the robust, portable win. The
> **runner-in-a-pod** works but is environment-specific (privileged + hostNetwork + node pinning +
> the UPF's L2 topology). If the pod can't reach the UPF's access macvlan on that node, injection
> fails the same way host injection would — see the troubleshooting table in
> `docs/BRING-YOUR-OWN-SDCORE.md`. When in doubt, run tests from the host CLI and just deploy the
> dashboard in‑cluster.

## Multi-node
Swap the `hostPath` campaigns volume for a `ReadWriteMany` PVC (NFS/Longhorn/CephFS) mounted by
both the dashboard and the runner, and pin the runner to the UPF's node with `nodeName`/affinity.
